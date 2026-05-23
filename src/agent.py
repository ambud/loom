"""Agent loop: tool execution, approval gating, display, compaction."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import asyncio

from openai import AsyncOpenAI, APIError

from .utils import pt_print, print_markdown, stream_print
from .tools.base import ToolRegistry
from .llm import async_count_tokens

READ_TOOLS = {"read_file", "glob_search", "grep_search", "memory", "git_status", "git_diff", "git_log", "bg_check", "plan", "complete_task", "fail_task"}
WRITE_TOOLS = {"write_file", "edit_file", "git_commit", "apply_diff", "replace_lines"}
EXEC_TOOLS = {"bash", "run_bg"}


async def _message_tokens(msg: dict, cfg: dict) -> int:
    """Estimate tokens for a single message, with caching."""
    # Use a hash of the content to avoid storing massive strings in the cache key
    text = msg.get("content", "") or ""
    tc_json = ""
    if "tool_calls" in msg:
        tc_json = json.dumps(msg["tool_calls"])
    
    # We use a simple hash of the content as the key
    content_sig = hash((text, tc_json))
    if msg.get("_tokens_sig") == content_sig:
        return msg.get("_tokens", 0)

    count = await async_count_tokens(text + tc_json, cfg)
    
    # Store in message dict (private keys)
    msg["_tokens_sig"] = content_sig
    msg["_tokens"] = count
    return count


async def _total_message_tokens(messages: list[dict], cfg: dict, start: int = 0) -> int:
    """Calculate total tokens for all messages[start:] with limited concurrency."""
    sem = asyncio.Semaphore(10) # Limit to 10 concurrent token counts
    
    async def _counted_tokens(msg):
        async with sem:
            return await _message_tokens(msg, cfg)
            
    counts = await asyncio.gather(*[_counted_tokens(messages[i]) for i in range(start, len(messages))])
    return sum(counts)


async def compact_messages(
    client: Any,
    messages: list[dict],
    cfg: dict,
    *,
    force: bool = False,
    tracker: Any = None,
    status: Any = None,
) -> None:
    """Summarize older messages to fit within context window."""
    window = cfg.get("context_window", 250000)
    threshold = cfg.get("compaction_threshold", 0.80)
    keep_turns = cfg.get("compaction_keep_last_turns", 3)

    total = await _total_message_tokens(messages, cfg)
    if not force and total < window * threshold:
        return

    if not force and total < window * 0.3:
        pt_print(
            f"Compaction skipped — only {total:,} tokens used.", "dim"
        )
        return

    # Keep system prompt (index 0) and last few turns
    # Each turn is roughly 2 messages (user + assistant)
    keep_messages = 1 + (keep_turns * 2)
    if len(messages) <= keep_messages + 2:
        return

    summarize_end = len(messages) - keep_messages
    to_summarize = messages[1:summarize_end]
    
    summary_prompt = (
        "Summarize the following conversation history between a user and a coding assistant. "
        "Maintain all core technical details, decisions made, and the current state of the project. "
        "Keep it concise but comprehensive."
    )
    
    summary_msg = {"role": "user", "content": f"{summary_prompt}\n\n{json.dumps(to_summarize)}"}
    
    try:
        # Add timeout to compaction call to prevent hanging the whole agent
        if status:
            status.update("Summarizing conversation for compaction...")

        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=cfg["model"],
                messages=[messages[0], summary_msg],
                max_tokens=4000,
                temperature=0.0,
            ),
            timeout=600.0
        )
        summary_text = response.choices[0].message.content
        
        # Replace middle with summary
        new_messages = [messages[0]]
        new_messages.append({"role": "system", "content": f"Previous conversation summary:\n{summary_text}"})
        new_messages.extend(messages[summarize_end:])
        
        messages[:] = new_messages
        new_total = await _total_message_tokens(messages, cfg)
        if tracker:
            tracker.update_current(new_total)
        pt_print(f"Context compacted: {total:,} -> {new_total:,} tokens.", "dim")
    except asyncio.TimeoutError:
        pt_print("Compaction timed out after 600s.", "red")
    except Exception as e:
        pt_print(f"Compaction failed: {e}", "red")


def _tool_icon(name: str) -> str:
    """Return an emoji icon for a tool."""
    if name in READ_TOOLS: return "r"
    if name in WRITE_TOOLS: return "w"
    if name in EXEC_TOOLS: return "x"
    return "t"


def _tool_style(name: str) -> str:
    """Return a color style for a tool."""
    if name in READ_TOOLS: return "cyan"
    if name in WRITE_TOOLS: return "magenta"
    if name in EXEC_TOOLS: return "green"
    return "blue"


def _short_args(args: dict) -> str:
    """Summarize tool arguments for display."""
    items = []
    for k, v in args.items():
        if isinstance(v, str):
            if len(v) > 100:
                v = v[:97] + "..."
            v = v.replace("\n", " ")
        items.append(f"{k}={v}")
    return " ".join(items)


def _needs_approval(name: str, mode: str, always_approved: set[str]) -> bool:
    """Check if a tool call requires user approval."""
    if name in always_approved:
        return False
    if mode == "yolo":
        return False
    if mode == "relay":
        return name in WRITE_TOOLS or name in EXEC_TOOLS
    return False


class ProgressMonitor:
    """Tracks session progress to detect stalls, loops, and errors."""

    def __init__(self, threshold: int = 3):
        self.history: list[dict] = []
        self.threshold = threshold
        self._last_content_hash = None
        self._content_repeat_count = 0
        self._tool_call_history = {}  # Dict of {args_tuple: count}
        self._error_count = 0
        self.task_completed = False
        self.nudge_count = 0
        self.max_nudges = 3
        self.nudged_in_turn = False

    def check(self, content: str | None, tool_calls: list[dict]) -> str | None:
        """Check for stalls or loops. Returns a hint message if a problem is detected."""
        
        # Track completion
        t_names = [tc.get("function", {}).get("name") for tc in tool_calls]
        if "complete_task" in t_names or "fail_task" in t_names:
            self.task_completed = True
            return None
        
        # Reset nudge-in-turn flag when tools are called
        if tool_calls:
            self.nudged_in_turn = False

        if content and len(content.strip()) > 50: # Only check significant content
            content_hash = hash(content.strip())
            if content_hash == self._last_content_hash:
                self._content_repeat_count += 1
            else:
                self._last_content_hash = content_hash
                self._content_repeat_count = 0
            
            if self._content_repeat_count >= self.threshold:
                return (
                    "STALL DETECTED: You have repeated the same technical explanation multiple times. "
                    "If you are stuck or your tools aren't giving you the info you need, try a different approach "
                    "or ask the user for clarification."
                )

        # 2. Check for tool call cycles
        if tool_calls:
            current_calls = []
            for tc in tool_calls:
                func = tc.get("function", {})
                current_calls.append((func.get("name"), func.get("arguments")))
            
            # Use tuple of calls as key to track frequency
            calls_key = tuple(current_calls)
            self._tool_call_history[calls_key] = self._tool_call_history.get(calls_key, 0) + 1
            
            # Only trigger if the EXACT same sequence happens 3 times in a single turn
            if self._tool_call_history[calls_key] >= 3:
                # Determine if these are mostly "read" tools
                all_read = all(name in READ_TOOLS or name == "bash" and "ls" in str(args) for name, args in current_calls)
                
                if all_read:
                    # Be more gentle for read-only repetition
                    return (
                        "OBSERVATION: You are repeating the same information-gathering commands. "
                        "If you already have the information you need, please proceed to the next step. "
                        "If the output was truncated or missing, try a more specific command."
                    )
                else:
                    return (
                        "LOOP DETECTED: You have attempted the same sequence of actions 3 times. "
                        "This strategy is not working. Please re-evaluate your assumptions, "
                        "check for hidden errors, and try a completely different technical approach."
                    )
        
        return None

    def report_tool_results(self, results: list[str]):
        """Track errors in tool results."""
        for res in results:
            if res.startswith("Error:") or "failed" in res.lower() or "not found" in res.lower():
                self._error_count += 1
            else:
                self._error_count = 0 # Reset on success
        
        if self._error_count >= 10:
            return (
                "PERSISTENT ERRORS: You have encountered multiple consecutive errors. "
                "Instead of retrying the same command, try to investigate the root cause "
                "using 'ls', 'cat', or 'read_file' to verify the environment state."
            )
        return None


async def execute_tools(
    tool_calls: list[dict],
    registry: ToolRegistry,
    cfg: dict,
    *,
    status: Any = None,
    input_fn: Any = None,
    always_approved: set[str] | None = None,
) -> tuple[list[str], bool]:
    """Execute a list of tool calls, handling approval and display."""
    if always_approved is None:
        always_approved = set()

    mode = cfg.get("approval_mode", "yolo")
    results = []
    stop_loop = False

    async def _get_input(prompt: str) -> str:
        """Call input_fn (async or sync) or builtin input()."""
        f = input_fn or input
        if asyncio.iscoroutinefunction(f):
            return await f(prompt)
        res = f(prompt)
        if asyncio.iscoroutine(res):
            return await res
        return res

    for tc in tool_calls:
        func = tc["function"]
        name = func["name"]

        # Parse args — LLM may return truncated/malformed JSON
        raw_args = func["arguments"]
        args: dict[str, Any] = {}
        try:
            args = json.loads(raw_args)
        except json.JSONDecodeError:
            try:
                # Attempt to repair truncated/malformed JSON
                depth = 0
                in_string = False
                escaped = False
                repaired_args = ""
                
                for i, ch in enumerate(raw_args):
                    repaired_args += ch
                    if escaped:
                        escaped = False
                        continue
                    if ch == '\\':
                        escaped = True
                        continue
                    if ch == '"':
                        in_string = not in_string
                        continue
                    if in_string:
                        continue
                    if ch == '{':
                        depth += 1
                    elif ch == '}':
                        depth -= 1

                # If still in a string, close it
                if in_string:
                    if repaired_args.endswith('\\'):
                        repaired_args = repaired_args[:-1]
                    repaired_args += '"'
                
                if depth > 0:
                    repaired_args += '}' * depth
                
                args = json.loads(repaired_args)
                pt_print(f"  [dim](auto-repaired truncated JSON)[/dim]")
            except json.JSONDecodeError:
                results.append(f"Tool {name}: malformed arguments — {raw_args[:200]}")
                continue

        icon = _tool_icon(name)

        if _needs_approval(name, mode, always_approved):
            pt_print(f"Approve? [tool]{name}[/tool]", style="bold yellow")
            pt_print(f"  {_short_args(args)}", style="dim", markup=False)
            answer = await _get_input('  [y]es  [n]o  [a]lways (this tool) (y): ')
            answer = (answer or "y").strip().lower()
            if answer == "n":
                results.append(f"Tool call declined by user.")
                continue
            if answer == "a":
                always_approved.add(name)

        # Tool call line
        pt_print(f"  [tool]{icon} {name}[/tool] {_short_args(args)}", style="dim")

        if name == "bash":
            if not cfg.get("allow_shell_commands", True):
                result = "Error: shell commands are disabled in this environment."
            else:
                try:
                    from .session import run_bash
                    result = await run_bash(args.get("command", ""), timeout=args.get("timeout", 60))
                except Exception as e:
                    result = f"Error running bash: {e}"
        elif name == "plan":
            steps = args.get("steps", [])
            rationale = args.get("rationale", "")
            pt_print("\nPROPOSED PLAN:", style="bold blue")
            for i, s in enumerate(steps, 1):
                pt_print(f"  {i}. {s}")
            if rationale:
                pt_print(f"Rationale: {rationale}", "dim")
            
            pt_print(f"Approve plan?", style="bold yellow")
            answer = await _get_input('  [y]es  [n]o  [c]omment (y): ')
            answer = (answer or "y").strip().lower()
            
            if answer == "y":
                result = "Plan approved by user. Proceeding with execution."
            elif answer == "c":
                feedback = await _get_input("Enter your feedback: ")
                result = f"Plan needs modification. User feedback: {feedback}"
                results.append(result)
                stop_loop = True
                break
            else:
                result = "Plan rejected by user. Propose a different approach."
                results.append(result)
                stop_loop = True
                break
        elif name == "complete_task":
            summary = args.get("summary", "")
            verification = args.get("verification", "")
            pt_print("\n[bold green]TASK COMPLETED[/bold green]")
            pt_print(f"Summary: {summary}", "green")
            pt_print(f"Verification: {verification}", "dim")
            results.append(f"Task completion acknowledged. Summary: {summary}")
            stop_loop = True
            break
        elif name == "fail_task":
            reason = args.get("reason", "")
            attempts = args.get("attempts", "")
            pt_print("\n[bold red]TASK FAILED[/bold red]")
            pt_print(f"Reason: {reason}", "red")
            pt_print(f"Attempts: {attempts}", "dim")
            results.append(f"Task failure acknowledged. Reason: {reason}")
            stop_loop = True
            break
        else:
            if status:
                status.update(f"Running {name}...")
            try:
                result = await registry.dispatch(name, **args)
                
                # Post-action hook
                check_cmd = cfg.get("post_action_check")
                if check_cmd and name in WRITE_TOOLS and not result.startswith("Error:"):
                    pt_print(f"\n  [dim]Running post-action check: {check_cmd}...[/dim]")
                    if status:
                        status.update(f"Checking {check_cmd}...")
                    
                    proc = await asyncio.create_subprocess_shell(
                        check_cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stdout, stderr = await proc.communicate()
                    exit_code = proc.returncode
                    
                    check_output = stdout.decode(errors="replace") + stderr.decode(errors="replace")
                    if len(check_output) > 2000:
                        check_output = check_output[:1000] + "\n...[truncated]...\n" + check_output[-1000:]
                    
                    status_msg = "PASSED" if exit_code == 0 else "FAILED"
                    pt_print(f"  [dim]Check {status_msg} (exit={exit_code})[/dim]")
                    
                    result += f"\n\n--- AUTOMATED POST-ACTION CHECK ({check_cmd}) ---\n"
                    result += f"Status: {status_msg}\n"
                    result += f"Output:\n{check_output}"
            except Exception as exc:
                result = f"Error running tool '{name}': {exc}"

        # Display result with tool-specific formatting
        if name == "bash":
            lines = result.split("\n")
            stdout_text = ""
            stderr_text = ""
            in_stdout = False
            in_stderr = False
            for l in lines:
                if l.startswith("STDOUT:"): in_stdout = True; in_stderr = False; continue
                if l.startswith("STDERR:"): in_stderr = True; in_stdout = False; continue
                if in_stdout: stdout_text += l + "\n"
                if in_stderr: stderr_text += l + "\n"
            
            if stdout_text:
                pt_print(f"  [STDOUT] {stdout_text.rstrip()}", style="green", markup=False)
            if stderr_text:
                pt_print(f"  [STDERR] {stderr_text.rstrip()}", style="red", markup=False)
        elif name == "read_file":
            lines = result.rstrip("\n").split("\n")
            preview_lines = lines[:6]
            if len(lines) > 6:
                preview_lines.append(f"  ...[{len(lines) - 6} more lines]...")
            pt_print("\n".join(preview_lines), style="green", markup=False)
        elif name == "glob_search":
            pt_print(f"  {result.strip()}", style="green", markup=False)
        elif name in ("write_file", "edit_file", "apply_diff", "replace_lines"):
            pt_print(f"  {result.strip()}", style="yellow", markup=False)
        else:
            pt_print(f"  {result.strip()}", style="green", markup=False)
        pt_print()

        results.append(result)

    return results, stop_loop


async def run_turns(
    client: Any,
    messages: list[dict],
    tools: list[dict],
    registry: ToolRegistry,
    cfg: dict,
    *,
    status: Any = None,
    tracker: Any = None,
    input_fn: Any = None,
    logger: Any = None,
) -> None:
    """LLM call -> tool exec loop. Appends messages in place."""
    if status:
        status.start()
    
    try:
        window = cfg.get("context_window", 250000)
        threshold = cfg.get("compaction_threshold", 0.80)
        do_stream = cfg.get("stream_text", True)
        plan_mode = cfg.get("plan", False)

        monitor = ProgressMonitor()

        max_rounds = cfg.get("max_tool_rounds", 500)
        for round_num in range(1, max_rounds + 1):
            await asyncio.sleep(0)

            if round_num == max_rounds:
                pt_print(f"\n[bold yellow]Warning:[/bold yellow] Reached maximum tool rounds ({max_rounds}).", style="yellow")

            if plan_mode and round_num == 1 and len(messages) <= 2:
                plan_instruction = (
                    "\n\n[PLAN MODE ENABLED]\n"
                    "You are in PLAN MODE. Use the `plan` tool to propose your step-by-step approach before executing other tools."
                )
                if messages[-1]["role"] == "user":
                    messages[-1]["content"] += plan_instruction
                else:
                    messages.append({"role": "user", "content": plan_instruction})

            current_total = await _total_message_tokens(messages, cfg)
            if tracker:
                tracker.update_current(current_total)
            pct = current_total / window * 100 if window else 0
            info = f"{current_total:,} tokens ({pct:.1f}%)"

            if current_total > int(window * threshold):
                if status:
                    status.update(f"Compacting... {info}")
                try:
                    await compact_messages(client, messages, cfg, tracker=tracker, status=status)
                    current_total = await _total_message_tokens(messages, cfg)
                    if tracker:
                        tracker.update_current(current_total)
                    pct = current_total / window * 100 if window else 0
                    info = f"{current_total:,} tokens ({pct:.1f}%)"
                except Exception:
                    raise

            try:
                if status:
                    status.update(f"Thinking... {info}")

                if round_num == 1:
                    pt_print()

                if do_stream:
                    content, tool_calls = await _stream_response(
                        client, messages, tools, cfg, status=status,
                    )
                else:
                    response = await client.chat.completions.create(
                        model=cfg["model"],
                        messages=messages,
                        tools=tools,
                        tool_choice="auto",
                        max_tokens=cfg.get("max_tokens", 4000),
                        temperature=cfg.get("temperature", 0.0),
                    )
                    content = response.choices[0].message.content
                    tool_calls = response.choices[0].message.tool_calls or []
                    tool_calls = [tc.model_dump() if hasattr(tc, "model_dump") else tc for tc in tool_calls]
                
                if content:
                    tokens = await async_count_tokens(content, cfg)
                    if tracker: tracker.add(tokens, is_output=True)
                if tool_calls:
                    for tc in tool_calls:
                        tc_text = json.dumps(tc)
                        tokens = await async_count_tokens(tc_text, cfg)
                        if tracker: tracker.add(tokens, is_output=True)

                assistant_msg: dict = {"role": "assistant", "content": content}
                if tool_calls:
                    assistant_msg["tool_calls"] = tool_calls
                messages.append(assistant_msg)
                if logger:
                    logger.log("assistant", content or "", tool_calls=tool_calls if tool_calls else None)

                stall_hint = monitor.check(content, tool_calls)
                if stall_hint:
                    pt_print(f"\n[bold red]Monitor:[/bold red] {stall_hint}", "dim")
                    messages.append({"role": "system", "content": stall_hint})

                if not tool_calls:
                    if not monitor.task_completed and not content and monitor.nudge_count < monitor.max_nudges and not monitor.nudged_in_turn:
                        nudge = (
                            "You have stopped calling tools, but you have not called `complete_task`. "
                            "If the task is truly finished, you MUST call `complete_task` with a summary of your work and verification. "
                            "If it is not finished, please continue working."
                        )
                        pt_print(f"\n[bold yellow]Monitor:[/bold yellow] Nudging for completion ({monitor.nudge_count + 1}/{monitor.max_nudges})...", "dim")
                        messages.append({"role": "system", "content": nudge})
                        monitor.nudge_count += 1
                        monitor.nudged_in_turn = True
                        continue
                    break

                if content:
                    pt_print()
                
                results, stop_loop = await execute_tools(
                    tool_calls, registry, cfg, 
                    status=status, 
                    input_fn=input_fn,
                )
                
                error_hint = monitor.report_tool_results(results)
                if error_hint:
                    pt_print(f"\n[bold red]Monitor:[/bold red] {error_hint}", "dim")
                    messages.append({"role": "system", "content": error_hint})

                for tc, result_text in zip(tool_calls[:len(results)], results):
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": result_text,
                        }
                    )
                    if logger:
                        logger.log("tool", result_text, tool_call_id=tc["id"])
                
                if stop_loop:
                    break
            except APIError as exc:
                pt_print(f"LLM error ({exc.code if hasattr(exc, 'code') else '?'}): {exc.message}", "red")
                break
    finally:
        if status:
            status.stop()


async def _stream_response(
    client: Any,
    messages: list[dict],
    tools: list[dict],
    cfg: dict,
    *,
    status: Any = None,
) -> tuple[str, list[dict]]:
    """Stream response from LLM and extract text + tool calls."""
    response = await client.chat.completions.create(
        model=cfg["model"],
        messages=messages,
        tools=tools,
        tool_choice="auto",
        stream=True,
        max_tokens=cfg.get("max_tokens", 4000),
        temperature=cfg.get("temperature", 0.0),
    )

    full_content = ""
    tool_calls = []
    tool_call_buffer = {}
    it = response.__aiter__()
    is_first_chunk = True

    while True:
        try:
            timeout = 300.0 if is_first_chunk else 120.0
            chunk = await asyncio.wait_for(it.__anext__(), timeout=timeout)
            
            # Stop "Thinking" status on first token to allow horizontal streaming
            if is_first_chunk and status:
                status.stop()
                
            is_first_chunk = False
        except StopAsyncIteration:
            break
        except asyncio.TimeoutError:
            if is_first_chunk:
                pt_print("Stream timed out — LLM failed to start responding within 300s", style="red")
            else:
                pt_print("Stream timed out — no data for 120s", style="red")
            break
        except APIError as exc:
            pt_print(f"\nStream interrupted by LLM error ({exc.code}): {exc.message}", "yellow")
            break

        delta = chunk.choices[0].delta
        if delta.content:
            full_content += delta.content
            if cfg.get("stream_text", True):
                stream_print(delta.content)

        if delta.tool_calls:
            for tc in delta.tool_calls:
                idx = tc.index
                if idx not in tool_call_buffer:
                    tool_call_buffer[idx] = {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    }
                if tc.function.name:
                    tool_call_buffer[idx]["function"]["name"] += tc.function.name
                if tc.function.arguments:
                    tool_call_buffer[idx]["function"]["arguments"] += tc.function.arguments

    for idx in sorted(tool_call_buffer.keys()):
        tool_calls.append(tool_call_buffer[idx])

    if full_content and not full_content.endswith("\n"):
        pt_print()

    return full_content, tool_calls
