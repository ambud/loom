from __future__ import annotations

import asyncio
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .utils import pt_print, print_panel, Markdown

class TokenTracker:
    """Tracks token usage for current session and across all sessions."""

    def __init__(self, session_id: str | None = None, log_dir: Path | None = None, tokens_db: Path | None = None, invalidate_ui_fn: Any = None):
        self.session_id = session_id
        self.log_dir = log_dir
        self.tokens_db = tokens_db or Path.home() / ".loom" / "tokens.json"
        self.invalidate_ui_fn = invalidate_ui_fn
        self.session_input = 0
        self.session_output = 0
        
        totals = self._load_totals()
        self.total_input = totals.get("input", 0)
        self.total_output = totals.get("output", 0)
        self.total_tokens = totals.get("total", 0)
        self.context_window = 250000

    @property
    def session_total(self) -> int:
        return self.session_input + self.session_output

    def _load_totals(self) -> dict:
        if self.tokens_db.exists():
            try:
                data = json.loads(self.tokens_db.read_text())
                if isinstance(data, int):
                    return {"total": data, "input": 0, "output": 0}
                return data
            except (json.JSONDecodeError, OSError):
                pass
        return {"total": 0, "input": 0, "output": 0}

    def _save_totals(self):
        self.tokens_db.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "total": self.total_tokens,
            "input": self.total_input,
            "output": self.total_output,
        }
        self.tokens_db.write_text(json.dumps(data, indent=2))

    def _save_session_metrics(self):
        if self.session_id and self.log_dir:
            metrics_path = self.log_dir / f"{self.session_id}_metrics.json"
            data = {
                "session_id": self.session_id,
                "input_tokens": self.session_input,
                "output_tokens": self.session_output,
                "total_tokens": self.session_total,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            metrics_path.write_text(json.dumps(data, indent=2))

    def add(self, n: int, is_output: bool = False):
        """Add tokens to the current tracking. Defaults to input tokens."""
        if is_output:
            self.session_output += n
            self.total_output += n
        else:
            self.session_input += n
            self.total_input += n
        
        self.total_tokens = self.total_input + self.total_output
        if self.invalidate_ui_fn:
            self.invalidate_ui_fn()

    def flush(self):
        self._save_totals()
        self._save_session_metrics()

    def get_stats_string(self) -> str:
        pct = (self.session_total / self.context_window * 100) if self.context_window else 0
        return (
            f"tokens:{self.session_total:,}/{self.context_window:,} "
            f"({pct:.1f}%)"
        )


def _make_session_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _log_dir(cfg: dict) -> Path:
    p = Path(cfg.get("session_log_dir", str(Path.home() / ".loom" / "sessions")))
    p.mkdir(parents=True, exist_ok=True)
    return p


class SessionLogger:
    """Logs conversation turns to a JSONL file."""

    def __init__(self, cfg: dict, session_id: str | None = None):
        self.path: Path | None = None
        self.fp = None
        sid = session_id or _make_session_id()
        d = _log_dir(cfg)
        self.path = d / f"{sid}.jsonl"
        
        # Load existing meta if resuming
        if self.path.exists():
            with open(self.path, "r", encoding="utf-8") as f:
                first_line = f.readline()
                if first_line:
                    try:
                        self._meta = json.loads(first_line)
                    except json.JSONDecodeError:
                        self._meta = {"id": sid, "created": datetime.now(timezone.utc).isoformat(), "cwd": os.getcwd()}
                else:
                    self._meta = {"id": sid, "created": datetime.now(timezone.utc).isoformat(), "cwd": os.getcwd()}
            # Append mode for resuming
            self.fp = open(self.path, "a", encoding="utf-8")
        else:
            self._meta = {
                "id": sid,
                "created": datetime.now(timezone.utc).isoformat(),
                "cwd": os.getcwd(),
            }
            self.fp = open(self.path, "w", encoding="utf-8")
            self.log("meta", "session started", **self._meta)

    def log(self, role: str, content: str, **extra):
        if self.fp:
            entry = {"role": role, "content": content, **extra}
            self.fp.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self.fp.flush()

    def load_messages(self) -> list[dict]:
        """Load conversation messages from the log file, skipping meta/system internal logs."""
        messages = []
        if not self.path or not self.path.exists():
            return messages
            
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    role = entry.get("role")
                    # We keep system, user, assistant, tool roles for the agent context
                    if role in ("system", "user", "assistant", "tool"):
                        # Remove any extra keys not needed by OpenAI API
                        msg = {"role": role, "content": entry.get("content", "")}
                        if "tool_calls" in entry:
                            msg["tool_calls"] = entry["tool_calls"]
                        if "tool_call_id" in entry:
                            msg["tool_call_id"] = entry["tool_call_id"]
                        messages.append(msg)
                except json.JSONDecodeError:
                    continue
        return messages

    def close(self):
        if self.fp:
            self.fp.close()
            self.fp = None


class BackgroundTask:
    def __init__(self, name: str, task: asyncio.Task):
        self.name = name
        self.task = task
        self.result = None
        self.done = False
        self.error = None

    def status_line(self) -> str:
        if self.done:
            if self.error:
                return f"#{self.name} -> FAILED: {self.error}"
            return f"#{self.name} -> DONE"
        if self.task.done():
            return f"#{self.name} -> completed"
        if self.task.cancelled():
            return f"#{self.name} -> cancelled"
        return f"#{self.name} -> running"


class BackgroundManager:
    def __init__(self, bg_file: Path | None = None):
        self._tasks: list[BackgroundTask] = []
        self.bg_file = bg_file or Path.home() / ".loom" / "background_tasks.json"

    def add(self, name: str, coro) -> BackgroundTask:
        task = asyncio.create_task(coro)
        bt = BackgroundTask(name, task)
        self._tasks.append(bt)
        return bt

    def list_tasks(self) -> list[BackgroundTask]:
        for bt in self._tasks:
            if bt.task.done() and not bt.done:
                bt.done = True
                if bt.task.cancelled():
                    bt.error = "cancelled"
                elif bt.task.exception():
                    bt.error = str(bt.task.exception())
        return self._tasks

    def status_panel(self) -> str:
        tasks = self.list_tasks()
        if not tasks:
            return "No background tasks."
        lines = []
        for bt in tasks:
            lines.append(bt.status_line())
        return "\n".join(lines)

    def save(self):
        self.bg_file.parent.mkdir(parents=True, exist_ok=True)
        data = []
        for bt in self._tasks:
            data.append({
                "name": bt.name,
                "done": bt.done,
                "error": bt.error,
            })
        self.bg_file.write_text(json.dumps(data, indent=2))


async def run_bash(cmd: str) -> None:
    """Execute a shell command directly, display output."""
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=os.getcwd(),
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode is not None:
        pt_print(f"exit={proc.returncode}", "bold")
    if stdout:
        pt_print(stdout.decode().rstrip(), "green")
    if stderr:
        pt_print(f"[STDERR] {stderr.decode().rstrip()}", "red")


async def run_bash_bg(cmd: str) -> None:
    """Execute a shell command in the background, print output when done."""
    name = cmd[:60] or "shell"
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=os.getcwd(),
    )
    stdout, stderr = await proc.communicate()
    out = stdout.decode().rstrip()
    err = stderr.decode().rstrip()
    pt_print(f"!! {cmd}  exit={proc.returncode}", "bold")
    if out:
        # Truncate long output
        if len(out) > 4000:
            out = out[:4000] + "\n... [truncated]"
        pt_print(out, "green")
    if err:
        pt_print(f"[STDERR] {err}", "red")
    pt_print()

async def run_review(
    client_cache: dict,
    messages: list[dict],
    cfg: dict,
    registry: Any,
    tracker: TokenTracker,
    status: Any = None,
    input_fn: Any = None
) -> None:
    """Run the git diff review process."""
    from .tools.git_tools import GitDiffTool
    from .config import load_review_system_prompt, get_model_cfg, make_client
    from .agent import run_turns
    from .llm import TOOLS

    git = GitDiffTool()
    unstaged = await git.run(staged=False)
    staged = await git.run(staged=True)
    diff_parts = []
    if staged and staged != "No changes staged":
        diff_parts.append("=== STAGED CHANGES ===\n" + staged)
    if unstaged and unstaged != "No changes":
        diff_parts.append("=== UNSTAGED CHANGES ===\n" + unstaged)
    diff_text = "\n\n".join(diff_parts) if diff_parts else "(no changes)"

    # Build a conversation summary from messages
    conv_summary = []
    for msg in messages[:]:
        role = msg.get("role", "?")
        content = str(msg.get("content", ""))[:2000]
        if role == "system":
            conv_summary.append(f"[system] {content[:500]}")
        elif role == "user":
            conv_summary.append(f"[user] {content}")
        elif role == "assistant" and content:
            conv_summary.append(f"[assistant] {content}")
    conv_text = "\n\n".join(conv_summary)

    review_system = load_review_system_prompt(cfg)
    review_profile = cfg.get("review_profile", "reviewer")
    review_cfg = get_model_cfg(cfg, review_profile) if review_profile in cfg.get("models", {}) else cfg
    
    if review_profile not in client_cache:
        client_cache[review_profile] = make_client(review_cfg)
    review_client = client_cache[review_profile]

    review_prompt = f"""Review our entire session. Analyze what was built, how it was built, and suggest improvements.

# Current Diff
{diff_text}

# Conversation Context
{conv_text}
"""
    review_messages = [{"role": "system", "content": review_system}, {"role": "user", "content": review_prompt}]

    pt_print()
    pt_print(f"Reviewing session with {review_cfg.get('model', '?')}...", "bold")
    
    await run_turns(
        review_client, 
        review_messages, 
        TOOLS, 
        registry, 
        review_cfg, 
        status=status, 
        tracker=tracker,
        input_fn=input_fn,
        logger=None # We don't necessarily want to log review turns to the main log, or we could.
    )
    
    # After review, the results are in review_messages
    findings = review_messages[-1]["content"] if review_messages[-1]["role"] == "assistant" else ""
    if findings:
        messages.append({
            "role": "user",
            "content": f"[SYSTEM] Code Review Findings:\n\n{findings}\n\nReview complete. Please address any critical issues or suggestions."
        })

def slash_help() -> None:
    help_text = (
        "**Conversation**\n"
        "- /compact: Manually compact history\n"
        "- /reload: Reload system prompt\n"
        "- /system: View current system prompt\n"
        "- /stats: Show token usage\n"
        "- /plan: Toggle plan mode\n\n"
        "**Tools & Shell**\n"
        "- /review: Review git diff\n"
        "- /bg: Run prompt in background\n"
        "- /background: List background tasks\n"
        "- !command: Run shell command\n\n"
        "**Knowledge**\n"
        "- /remember: Store a memory\n"
        "- /search: Search memories\n"
        "- /memory: List topics\n\n"
        "**Session**\n"
        "- /model: Switch profile\n"
        "- /config: Show settings\n"
        "- /session: Show session info\n"
        "- /quit: Exit REPL"
    )
    pt_print()
    print_panel(Markdown(help_text), title="Commands", style="tool")
    pt_print()


def slash_config(cfg: dict) -> None:
    lines = []
    for k, v in sorted(cfg.items()):
        if k == "models": continue # too verbose
        lines.append(f"**{k}**: {v}")
    
    pt_print()
    print_panel(Markdown("\n".join(lines)), title="Configuration", style="info")
    pt_print()


def slash_session(logger: SessionLogger) -> None:
    info = f"**ID**: {logger._meta.get('id', '?')}\n"
    info += f"**Log**: {logger.path}\n"
    info += f"**Started**: {logger._meta.get('created', '?')}"
    
    pt_print()
    print_panel(Markdown(info), title="Session", style="info")
    pt_print()
