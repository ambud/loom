"""Agent loop: tool execution, approval gating, display, compaction."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import asyncio

from openai import APIError

from .utils import pt_print, print_markdown
from .tools.base import ToolRegistry
from .llm import async_count_tokens

READ_TOOLS = {"read_file", "glob_search", "grep_search", "memory", "git_status", "git_diff", "git_log", "bg_check", "plan"}
WRITE_TOOLS = {"write_file", "edit_file", "git_commit"}
EXEC_TOOLS = {"bash", "run_bg"}


async def _message_tokens(msg: dict, cfg: dict) -> int:
    """Estimate tokens for a single message."""
    text = msg.get("content", "") or ""
    if "tool_calls" in msg:
        text += json.dumps(msg["tool_calls"])
    return await async_count_tokens(text, cfg)


async def _total_message_tokens(messages: list[dict], cfg: dict, start: int = 0) -> int:
    """Calculate total tokens for all messages[start:] concurrently."""
    counts = await asyncio.gather(*[_message_tokens(messages[i], cfg) for i in range(start, len(messages))])
    return sum(counts)


async def compact_messages(
    client: Any,
    messages: list[dict],
    cfg: dict,
    *,
    force: bool = False,
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
        response = await client.chat.completions.create(
            model=cfg["model"],
            messages=[messages[0], summary_msg],
            max_tokens=4000,
            temperature=0.0,
        )
        summary_text = response.choices[0].message.content
        
        # Replace middle with summary
        new_messages = [messages[0]]
        new_messages.append({"role": "system", "content": f"Previous conversation summary:\n{summary_text}"})
        new_messages.extend(messages[summarize_end:])
        
        messages[:] = new_messages
        new_total = await _total_message_tokens(messages, cfg)
        pt_print(f"Context compacted: {total:,} -> {new_total:,} tokens.", "dim")
    except Exception as e:
        pt_print(f"Compaction failed: {e}", "red")


def _tool_icon(name: str) -> str:
    """Return an emoji icon for a tool."""
    if name in READ_TOOLS: return "r"
    if name in WRITE_TOOLS: return "w"
    if name in EXEC_TOOLS: return "x"
    return "t"


def _tool_style(name: str) -> str:
    """Return the style for a tool."""
    if name in READ_TOOLS:
        return "green"
    if name in WRITE_TOOLS:
        return "yellow"
    return "red"


def _short_args(args: dict) -> str:
    """Return a short string representation of tool arguments."""
    parts = []
    for k, v in args.items():
        val = str(v)
        if len(val) > 40:
            val = val[:37] + "..."
        parts.append(f"{k}={repr(val)}")
    return ", ".join(parts)


def _needs_approval(name: str, mode: str, always_approved: set[str]) -> bool:
    """Return True if the tool call requires user approval."""
    if name in always_approved:
        return False
    if mode == "safe":
        return True
    if mode == "relay":
        return name in WRITE_TOOLS or name in EXEC_TOOLS
    return False


async def execute_tools(
    tool_calls: list[dict],
    registry: ToolRegistry,
    cfg: dict,
    *,
    status: Any = None,
    input_fn: Any = None,
) -> tuple[list[str], bool]:
    """Run tool calls sequentially (for approval prompts) with styled display.
    Returns (results, stop_requested_flag)."""

    mode = cfg.get("approval_mode", "yolo")
    always_approved: set[str] = set()
    results: list[str] = []
    stop_loop = False

    # Default to synchronous input (in a thread) if no function provided
    if input_fn is None:
        async def _sync_input(prompt=""):
            return await asyncio.get_event_loop().run_in_executor(None, input, prompt)
        input_fn = _sync_input

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
                    # If the last char is a \, it was an incomplete escape
                    if repaired_args.endswith('\\'):
                        repaired_args = repaired_args[:-1]
                    repaired_args += '"'
                
                # Close any remaining braces
                if depth > 0:
                    repaired_args += '}' * depth
                
                args = json.loads(repaired_args)
                pt_print(f"  [dim](auto-repaired truncated JSON)[/dim]")
            except json.JSONDecodeError:
                results.append(f"Tool {name}: malformed arguments — {raw_args[:200]}")
                continue

        icon = _tool_icon(name)
        style = _tool_style(name)

        if _needs_approval(name, mode, always_approved):
            pt_print(f"Approve? [tool]{name}[/tool]", style="bold yellow")
            pt_print(f"  {_short_args(args)}", style="dim", markup=False)
            answer = await input_fn('  [y]es  [n]o  [a]lways (this tool) (y): ')
            answer = (answer or "y").strip()
            if answer == "n":
                results.append(f"Tool call declined by user.")
                continue
            if answer == "a":
                always_approved.add(name)

        # Tool call line
        pt_print(f"  [tool]{icon} {name}[/tool] {_short_args(args)}", style="dim")

        if name == "bash":
            if not cfg.get("allow_shell_commands", True):
                results.append("Error: bash tool is disabled by administrator configuration.")
                pt_print("  [red]Error: bash tool is disabled[/red]")
                continue

        if name in ("read_file", "write_file", "edit_file"):
            def _get_root():
                # Attempt to get workspace root from config
                r = cfg.get("workspace_root", "")
                if r and r != ".":
                    try:
                        return os.path.abspath(os.path.expanduser(r))
                    except Exception:
                        return r
                
                # Fallback to CWD
                try:
                    return os.getcwd()
                except Exception:
                    return "/" # Absolute fallback

            root = _get_root()
            target_path = args.get("path", "")
            
            try:
                # Resolve the target path relative to root if it's relative
                if not os.path.isabs(target_path):
                    target = os.path.normpath(os.path.join(root, target_path))
                else:
                    target = os.path.normpath(target_path)
            except Exception:
                target = target_path

            if not target.startswith(root):
                results.append(f"Error: Path '{target_path}' is outside the allowed workspace root ({root}).")
                pt_print(f"  [red]Error: Path traversal blocked[/red]")
                continue

        if name == "plan":
            # Plan tool is handled specially to pause and wait for user approval
            steps = args.get("steps", [])
            rationale = args.get("rationale", "")
            
            pt_print("\n[bold cyan]PROPOSED PLAN:[/bold cyan]")
            if rationale:
                pt_print(f"Rationale: {rationale}", "dim")
            for i, step in enumerate(steps, 1):
                pt_print(f"  {i}. {step}")
            
            # Use input_fn with the prompt directly to ensure correct cursor positioning
            answer = await input_fn("\nApprove plan? [y/n/c] (y=approve, n=reject, c=comment) (y): ")
            answer = (answer or "y").lower().strip()
            
            if answer == "y":
                result = "Plan approved by user. Proceeding with execution."
                results.append(result)
            elif answer == "c":
                feedback = await input_fn("Enter your feedback: ")
                result = f"Plan needs modification. User feedback: {feedback}"
                results.append(result)
                stop_loop = True
                break  # Stop execution of subsequent tools in this turn
            else:
                result = "Plan rejected by user. Propose a different approach."
                results.append(result)
                stop_loop = True
                break  # Stop execution of subsequent tools in this turn
        else:
            if status:
                status.update(f"Running {name}...")
            try:
                result = await registry.dispatch(name, **args)
            except Exception as e:
                result = f"Error: {e}"

        # Display result with tool-specific formatting
        if name == "bash":
            # Parse "exit=N\nSTDOUT:\n...\nSTDERR:\n..." format
            exit_code = "?"
            stdout_text = ""
            stderr_text = ""
            lines = result.split("\n")
            section = None
            for line in lines:
                if line.startswith("exit="):
                    exit_code = line.replace("exit=", "").strip()
                elif line == "STDOUT:":
                    section = "stdout"
                elif line == "STDERR:":
                    section = "stderr"
                else:
                    if section == "stdout":
                        stdout_text += line + "\n"
                    elif section == "stderr":
                        stderr_text += line + "\n"
            
            color = "green" if exit_code == "0" else "red"
            pt_print(f"  exit={exit_code}", style=f"bold {color}")
            if stdout_text:
                pt_print(stdout_text.rstrip()[:2000], style="dim", markup=False)
            if stderr_text:
                pt_print(f"  [STDERR] {stderr_text.rstrip()}", style="red", markup=False)
        elif name == "read_file":
            # Result already has header "--- path ---\n     1  ...", show truncated preview
            lines = result.rstrip("\n").split("\n")
            preview_lines = lines[:6]
            if len(lines) > 6:
                preview_lines.append(f"  ...[{len(lines) - 6} more lines]...")
            pt_print("\n".join(preview_lines), style="green", markup=False)
        elif name == "glob_search":
            pt_print(f"  {result.strip()}", style="green", markup=False)
        elif name in ("write_file", "edit_file"):
            pt_print(f"  {result.strip()}", style="yellow", markup=False)
        else:
            pt_print(f"  {result.strip()}", style="green", markup=False)
        pt_print()  # blank line after tool result

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

        async def _ctx_info() -> str:
            tokens = await _total_message_tokens(messages, cfg)
            pct = tokens / window * 100 if window else 0
            return f"{tokens:,} tokens ({pct:.1f}%)"

        for round_num in range(1, cfg.get("max_tool_rounds", 25) + 1):
            await asyncio.sleep(0)  # Yield for cancellation

            # Inject plan mode instructions on the first turn of the session
            if plan_mode and round_num == 1 and len(messages) <= 2:
                plan_instruction = (
                    "\n\n[PLAN MODE ENABLED]\n"
                    "You are in PLAN MODE. Use the `plan` tool to propose your step-by-step approach before executing other tools."
                )
                for msg in reversed(messages):
                    if msg["role"] == "user":
                        msg["content"] += plan_instruction
                        break

            # Compact before each LLM call if context is getting tight
            if await _total_message_tokens(messages, cfg) > int(window * threshold):
                info = await _ctx_info()
                if status:
                    status.update(f"Compacting... {info}")
                try:
                    await compact_messages(client, messages, cfg)
                except Exception:
                    raise

            try:
                info = await _ctx_info()
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
                        max_tokens=cfg["max_tokens"],
                        temperature=cfg["temperature"],
                        tools=tools,
                        tool_choice="auto",
                    )
                    if hasattr(response, "model_dump"):
                        raw = response.model_dump()
                    elif isinstance(response, dict):
                        raw = response
                    else:
                        raw = {"choices": [{"message": {"content": str(response)}}]}

                    choice = raw["choices"][0]
                    msg = choice.get("message", {})
                    content = msg.get("content")
                    tool_calls = msg.get("tool_calls") or []
                    
                    # Record usage from non-streaming response
                    if tracker and "usage" in raw:
                        usage = raw["usage"]
                        tracker.add(usage.get("prompt_tokens", 0), is_output=False)
                        tracker.add(usage.get("completion_tokens", 0), is_output=True)

                if content:
                    pt_print()  # blank line before LLM text
                    print_markdown(content)
                    
                # If streaming, we count tokens manually at the end if tracker is present
                if do_stream and tracker:
                    # Count input (messages list before adding current assistant response)
                    # This is a fallback if the API doesn't provide it in stream (OpenAI doesn't always)
                    prompt_tokens = await _total_message_tokens(messages, cfg)
                    tracker.add(prompt_tokens, is_output=False)
                    
                    if content:
                        completion_tokens = await async_count_tokens(content, cfg)
                        tracker.add(completion_tokens, is_output=True)
                    # For tool calls in streaming, we approximate
                    for tc in tool_calls:
                        tc_text = json.dumps(tc)
                        tracker.add(await async_count_tokens(tc_text, cfg), is_output=True)

                assistant_msg: dict = {"role": "assistant", "content": content}
                if tool_calls:
                    assistant_msg["tool_calls"] = tool_calls
                messages.append(assistant_msg)
                if logger:
                    logger.log("assistant", content or "", tool_calls=tool_calls if tool_calls else None)

                if not tool_calls:
                    break

                if content:
                    print()
                await asyncio.sleep(0)  # Yield for cancellation
                results, stop_loop = await execute_tools(tool_calls, registry, cfg, status=status, input_fn=input_fn)
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
                pt_print("Continuing — you can try again.", "dim")
                sys.stdout.flush()
                break
    finally:
        if status:
            status.stop()


async def _stream_response(
    client: Any,
    messages: list[dict],
    tools: list[dict],
    cfg: dict,
    status: Any = None,
) -> tuple[str, list]:
    """Stream response with per-chunk timeout. Task cancellation is handled by the caller."""
    response = await client.chat.completions.create(
        model=cfg["model"],
        messages=messages,
        max_tokens=cfg["max_tokens"],
        temperature=cfg["temperature"],
        tools=tools,
        tool_choice="auto",
        stream=True,
    )

    full_content = ""
    tool_calls = []
    tool_call_buffer = {}
    it = response.__aiter__()
    tokens_seen = 0

    while True:
        try:
            chunk = await asyncio.wait_for(it.__anext__(), timeout=120.0)
        except StopAsyncIteration:
            break
        except asyncio.TimeoutError:
            pt_print("Stream timed out — no data for 120s", style="red")
            break
        except APIError as exc:
            pt_print(f"\nStream interrupted by LLM error ({exc.code}): {exc.message}", "yellow")
            pt_print("Attempting to salvage partial response...", "dim")
            break

        for choice in chunk.choices:
            delta = choice.delta
            if delta.content:
                full_content += delta.content
                tokens_seen += 1 # rough estimate
                if status and tokens_seen % 5 == 0:
                    status.update(f"Generating... ({tokens_seen} tokens)")
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_call_buffer:
                        tool_call_buffer[idx] = {"type": "function", "id": tc.id or "", "function": {"name": "", "arguments": ""}}
                    if tc.id:
                        tool_call_buffer[idx]["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            tool_call_buffer[idx]["function"]["name"] = tc.function.name
                        if tc.function.arguments:
                            tool_call_buffer[idx]["function"]["arguments"] += tc.function.arguments

    for tc in tool_call_buffer.values():
        if tc["function"]["arguments"]:
            try:
                args = json.loads(tc["function"]["arguments"])
                tc["function"]["arguments"] = json.dumps(args)
            except json.JSONDecodeError:
                pass
        tool_calls.append(tc)

    if full_content and not full_content.endswith("\n"):
        pt_print()

    return full_content, tool_calls
