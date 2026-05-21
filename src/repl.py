"""Polished REPL with prompt-toolkit for concurrent input, session logging, and token stats."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import re
import shutil

from rich.live import Live
from rich.text import Text as RichText
from rich.spinner import Spinner
from rich.columns import Columns
from rich.console import Console

from prompt_toolkit import PromptSession, HTML, ANSI
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.styles import Style

from .config import load_system_prompt, load_review_system_prompt, get_model_cfg, make_client
from .llm import async_count_tokens
from .utils import pt_print, print_panel, print_markdown, RichText, Markdown
from .session import (
    TokenTracker, SessionLogger, BackgroundManager, 
    run_bash, run_bash_bg, _log_dir,
    slash_help, slash_config, slash_session, run_review
)

# Strip ANSI escape codes — the user's interface can't render them
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _cwd_prompt() -> HTML:
    """Build a prompt like: ~/project>"""
    cwd = os.getcwd()
    home = os.path.expanduser("~")
    if cwd.startswith(home):
        short = cwd.replace(home, "~", 1)
    else:
        short = cwd
    return HTML(f"<style color='#3399ff' font-weight='bold'>{short}&gt; </style>")


HISTORY_FILE = str(Path.home() / ".loom" / "history")
TOKENS_DB = Path.home() / ".loom" / "tokens.json"
BG_FILE = Path.home() / ".loom" / "background_tasks.json"

# Custom style for the toolbar
LOOM_STYLE = Style.from_dict({
    "bottom-toolbar": "#aaaaaa bg:#222222",
})


class LoomCompleter(Completer):
    """Custom completer for Loom REPL (slash commands, shell commands, file paths)."""

    def __init__(self):
        self._path_cmds = self._get_path_commands()

    def _get_path_commands(self) -> list[str]:
        cmds = set()
        for d in os.environ.get("PATH", "").split(os.pathsep):
            if os.path.isdir(d):
                try:
                    for name in os.listdir(d):
                        if os.access(os.path.join(d, name), os.X_OK):
                            cmds.add(name)
                except OSError:
                    continue
        return sorted(list(cmds))

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor

        # Slash commands
        if text.startswith("/"):
            commands = [
                "/background", "/bg", "/clear", "/compact", "/config",
                "/help", "/memory", "/model", "/plan", "/quit", "/reload", "/remember",
                "/review", "/search", "/session", "/stats", "/system"
            ]
            for cmd in commands:
                if cmd.startswith(text):
                    yield Completion(cmd, start_position=-len(text))
            return

        # Shell commands
        if text.startswith("!"):
            cmd_part = text[1:]
            if " " not in cmd_part:
                for c in self._path_cmds:
                    if c.startswith(cmd_part):
                        yield Completion(f"!{c}", start_position=-len(text))

            # File completion for shell commands
            parts = cmd_part.split()
            if len(parts) > 0:
                last_part = parts[-1] if not text.endswith(" ") else ""
                search_dir = os.path.dirname(last_part) or "."
                if os.path.isdir(search_dir):
                    try:
                        base = os.path.basename(last_part)
                        for name in os.listdir(search_dir):
                            if name.startswith(base):
                                fp = os.path.join(search_dir, name)
                                display = name + ("/" if os.path.isdir(fp) else "")
                                yield Completion(display, start_position=-len(base))
                    except OSError:
                        pass
            return


_GLOBAL_SESSION: PromptSession | None = None


def invalidate_ui():
    """Force a redraw of the prompt-toolkit TUI."""
    if _GLOBAL_SESSION and _GLOBAL_SESSION.app:
        _GLOBAL_SESSION.app.invalidate()


tracker = TokenTracker(invalidate_ui_fn=invalidate_ui)
_GLOBAL_STATUS = ""
_GLOBAL_MODEL = ""
_GLOBAL_MODE = ""
_GLOBAL_PLAN = False
_GLOBAL_SPINNER = ""


_SHOW_TOOLBAR = True


def _get_toolbar():
    global _GLOBAL_STATUS, tracker, _GLOBAL_MODEL, _GLOBAL_MODE, _GLOBAL_PLAN, _GLOBAL_SPINNER, _SHOW_TOOLBAR
    if not _SHOW_TOOLBAR:
        return None

    stats = tracker.get_stats_string()
    cwd = os.getcwd()
    home = os.path.expanduser("~")
    short_cwd = cwd.replace(home, "~", 1) if cwd.startswith(home) else cwd

    # Metadata part
    meta = []
    if _GLOBAL_MODEL:
        meta.append(f"m:{_GLOBAL_MODEL}")
    if _GLOBAL_MODE:
        meta.append(f"mode:{_GLOBAL_MODE}")
    if _GLOBAL_PLAN:
        meta.append("PLAN")
    meta_str = "|".join(meta)

    # Calculate available space to prevent multi-line
    status_part = f"{_GLOBAL_STATUS} | " if _GLOBAL_STATUS else ""
    suffix = f" | {meta_str} | cwd:{short_cwd}"

    # If too long, truncate CWD first
    if len(status_part + stats + suffix) > 100:
        max_cwd = 20
        if len(short_cwd) > max_cwd:
            short_cwd = "..." + short_cwd[-(max_cwd-3):]
            suffix = f" | {meta_str} | cwd:{short_cwd}"

    left_part = f" {status_part}{stats}{suffix} "
    
    spinner_part = f"{_GLOBAL_SPINNER} " if _GLOBAL_SPINNER else ""
    right_part = f"{spinner_part}loom "
    
    try:
        width = shutil.get_terminal_size().columns
    except Exception:
        width = 100
        
    spaces = " " * max(0, width - len(left_part) - len(right_part))
    return [("class:bottom-toolbar", left_part + spaces + right_part)]


async def _run_agent_background(
    client: Any,
    system_prompt: str,
    user_prompt: str,
    messages: list[dict],
    tools: list[dict],
    registry: Any,
    cfg: dict,
    bg_mgr: BackgroundManager,
    logger: SessionLogger,
) -> None:
    """Run a single agent turn in the background using current conversation context."""
    from .agent import run_turns

    # Copy current messages and append the user's background prompt
    bg_messages = list(messages)
    
    plan_mode = cfg.get("plan", False)
    content = user_prompt
    if plan_mode:
        content += (
            "\n\n[PLAN MODE ENABLED]\n"
            "You are in PLAN MODE. Use the `plan` tool to propose your approach before executing other tools."
        )
    bg_messages.append({"role": "user", "content": content})

    pt_print(f"Background task started: {user_prompt[:80]}\n", "dim")
    logger.log("user_bg", user_prompt)

    async def auto_input_fn(prompt=""):
        return "y"

    try:
        await run_turns(client, bg_messages, tools, registry, cfg, tracker=tracker, input_fn=auto_input_fn, logger=logger)
    except Exception as exc:
        pt_print(f"\nBackground task failed: {exc}", "dim")
        logger.log("bg_error", str(exc))


from rich.table import Table

class StatusBar:
    """Manages a status message displayed in a persistent Rich Live bar."""

    def __init__(self, tracker: TokenTracker):
        self.tracker = tracker
        self.live = None
        self.status_text = ""
        self.active = False
        # Use a standard console that will be patched by prompt-toolkit
        self._console = Console()
        self._frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        self._frame_idx = 0

    def _get_renderable(self) -> Table:
        # Build the bar content
        stats = self.tracker.get_stats_string()
        
        # Metadata part
        meta = []
        if _GLOBAL_MODEL:
            meta.append(f"m:{_GLOBAL_MODEL}")
        if _GLOBAL_MODE:
            meta.append(f"mode:{_GLOBAL_MODE}")
        if _GLOBAL_PLAN:
            meta.append("PLAN")
        meta_str = "|".join(meta)
        
        status_part = f"{self.status_text} | " if self.status_text else ""
        left_text = f" {status_part}{stats} | {meta_str} "
        
        spinner = self._frames[self._frame_idx % len(self._frames)]
        self._frame_idx += 1
        
        # Create a single-row table to handle absolute right-alignment
        table = Table.grid(expand=True)
        table.add_column(justify="left")
        table.add_column(justify="right")
        
        table.add_row(
            RichText(left_text, style="black on #eeeeee"),
            RichText.assemble(
                (f"{spinner} ", "green bold"), 
                "loom ", 
                style="black on #eeeeee"
            )
        )
        return table

    def update(self, status_text: str) -> None:
        self.status_text = status_text

    def start(self) -> None:
        if not self.active:
            self.active = True
            
            # 1. Disable the prompt-toolkit toolbar
            global _SHOW_TOOLBAR
            _SHOW_TOOLBAR = False
            invalidate_ui()
            
            # 2. Manually clear the bottom line to ensure the old toolbar is gone
            sys.stdout.write("\r\x1b[K")
            sys.stdout.flush()
            
            from .utils import set_active_live
            
            # 3. Start Live display on standard stdout (managed by patch_stdout)
            self.live = Live(
                self._get_renderable(), 
                console=self._console, 
                refresh_per_second=12,
                transient=True, # Clear on completion
                get_renderable=self._get_renderable,
                auto_refresh=True
            )
            self.live.start()
            set_active_live(self.live)

    def stop(self) -> None:
        if self.active:
            self.active = False
            from .utils import set_active_live
            set_active_live(None)
            
            if self.live:
                self.live.stop()
                self.live = None
            
            # 4. Re-enable the prompt-toolkit toolbar
            global _SHOW_TOOLBAR
            _SHOW_TOOLBAR = True
            self.status_text = ""
            invalidate_ui()

    def __enter__(self) -> "StatusBar":
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()


class ActiveClient:
    """Mutable wrapper around an AsyncOpenAI client for runtime swapping."""

    def __init__(self, client: Any):
        self.cache = client

    def __getattr__(self, name: str):
        return getattr(self.cache, name)


async def interactive_loop(
    client: Any,
    system_prompt: str,
    cfg: dict,
    tools: list[dict],
    registry: Any,
    *,
    active_profile: str = "default",
) -> None:
    """Full-featured REPL loop with concurrent input support."""
    from .agent import run_turns, compact_messages, _total_message_tokens

    global tracker, _GLOBAL_MODEL, _GLOBAL_MODE, _GLOBAL_SESSION, _GLOBAL_PLAN

    bg_mgr = BackgroundManager()
    tracker.context_window = cfg.get("context_window", 250000)

    # Client cache: one AsyncOpenAI per named model profile
    client_cache: dict[str, Any] = {"default": client}
    active_client = ActiveClient(client)
    _ACTIVE_PROFILE = active_profile
    cfg["active_profile"] = active_profile

    _GLOBAL_MODEL = cfg.get("model", "llama")
    _GLOBAL_MODE = cfg.get("approval_mode", "yolo")
    _GLOBAL_PLAN = cfg.get("plan", False)

    status = StatusBar(tracker)

    pt_print()
    print_panel(
        RichText("privacy first coding agent cli", style="dim italic"),
        title="loom",
        style="success"
    )
    pt_print("Type your prompt anytime. Use [tool]!cmd[/tool] for shell, [tool]/help[/tool] for more.", style="dim")
    pt_print()

    logger = SessionLogger(cfg)
    logger.log("system", system_prompt)

    # Initialize tracker with session info
    tracker.session_id = logger._meta.get("id")
    tracker.log_dir = _log_dir(cfg)

    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    tracker.add(await async_count_tokens(system_prompt, cfg))

    session = PromptSession(
        history=FileHistory(HISTORY_FILE),
        completer=LoomCompleter(),
        reserve_space_for_menu=0,
        bottom_toolbar=_get_toolbar,
        style=LOOM_STYLE,
    )
    _GLOBAL_SESSION = session
    agent_task: asyncio.Task | None = None

    # Background status watcher
    bg_check_done = asyncio.Event()
    bg_check_task = asyncio.create_task(_bg_status_watcher(bg_mgr, bg_check_done))

    try:
        while True:
            with patch_stdout():
                try:
                    line = await session.prompt_async(_cwd_prompt())
                except (EOFError, KeyboardInterrupt):
                    if agent_task and not agent_task.done():
                        agent_task.cancel()
                        pt_print("Task cancelled.", "yellow")
                        agent_task = None
                        continue
                    pt_print("Goodbye.", "red bold")
                    pt_print(f"Session ID: {logger._meta.get('id')} (saved to {logger.path})", "dim")
                    break

            line = line.strip()
            if not line:
                continue

            # Direct shell execution: !command (foreground), !!command (background)
            if line.startswith("!!"):
                bg_mgr.add("shell", run_bash_bg(line[2:]))
                pt_print(f"Background shell started: {line[2:]}", "green")
                continue
            if line.startswith("!"):
                await run_bash(line[1:])
                continue

            # Slash commands
            if line.startswith("/"):
                parts = line[1:].split(None, 1)
                cmd = parts[0].lower()
                arg = parts[1] if len(parts) > 1 else ""

                if cmd in ("quit", "exit", "q"):
                    break
                if cmd == "help":
                    slash_help()
                    continue
                if cmd == "clear":
                    os.system("clear")
                    continue
                if cmd == "system":
                    pt_print(system_prompt)
                    continue
                if cmd == "reload":
                    system_prompt = load_system_prompt(cfg)
                    messages[0]["content"] = system_prompt
                    logger.log("system", system_prompt, note="reloaded")
                    pt_print("System prompt reloaded.\n", "green")
                    continue
                if cmd == "config":
                    slash_config(cfg)
                    continue
                if cmd == "session":
                    slash_session(logger)
                    continue
                if cmd == "stats":
                    pt_print(f"Session: {tracker.session_input:,} input | {tracker.session_output:,} output | {tracker.session_total:,} total", "dim")
                    pt_print(f"Global:  {tracker.total_input:,} input | {tracker.total_output:,} output | {tracker.total_tokens:,} total", "dim")
                    continue
                if cmd == "compact":
                    await compact_messages(active_client, messages, cfg, force=True)
                    tracker.session_tokens = await _total_message_tokens(messages, cfg)
                    continue

                if cmd == "plan":
                    _GLOBAL_PLAN = not _GLOBAL_PLAN
                    cfg["plan"] = _GLOBAL_PLAN
                    status_str = "ENABLED" if _GLOBAL_PLAN else "DISABLED"
                    pt_print(f"Plan mode {status_str}.", "green")
                    if _GLOBAL_PLAN:
                        pt_print("Agent will now use the `plan` tool to propose steps before execution.", "dim")
                        # Inject a reminder into the conversation
                        messages.append({
                            "role": "user", 
                            "content": "[SYSTEM] Plan mode enabled. Please use the `plan` tool to propose your approach for the current or next task."
                        })
                    invalidate_ui()
                    continue

                if cmd == "model":
                    models = cfg.get("models", {})
                    if not arg:
                        pt_print(f"Active: {_ACTIVE_PROFILE} ({cfg.get('model', '?')})", "bold")
                        if models:
                            pt_print("Profiles:", "dim")
                            for name in sorted(models):
                                profile = models[name]
                                marker = " *" if name == _ACTIVE_PROFILE else ""
                                pt_print(f"  {name}: {profile.get('model', cfg['model'])} ({profile.get('base_url', cfg['base_url'])}){marker}")
                        else:
                            pt_print("No profiles configured (see ~/.loom.yaml)", "dim")
                        continue
                    if arg in models:
                        _ACTIVE_PROFILE = arg
                        cfg["active_profile"] = _ACTIVE_PROFILE
                        # Apply profile overrides
                        profile = models[arg]
                        if "model" in profile:
                            _GLOBAL_MODEL = profile["model"]
                        # Swap the actual LLM client
                        if arg not in client_cache:
                            review_cfg = get_model_cfg(cfg, arg)
                            client_cache[arg] = make_client(review_cfg)
                        active_client.cache = client_cache[arg]
                        invalidate_ui()
                        pt_print(f"Switched to profile: {arg} (model={_GLOBAL_MODEL})", "green")
                        continue
                    pt_print(f"Unknown profile: {arg}. Available: {', '.join(sorted(models)) or '(none)'}", "red")
                    continue

                if cmd == "review":
                    # Temporarily swap active client context for the review turn (display purposes)
                    review_profile = cfg.get("review_profile", "reviewer")
                    review_cfg = get_model_cfg(cfg, review_profile) if review_profile in cfg.get("models", {}) else cfg
                    prev_active = _ACTIVE_PROFILE
                    prev_model = _GLOBAL_MODEL
                    _ACTIVE_PROFILE = review_profile
                    _GLOBAL_MODEL = review_cfg.get("model", "?")
                    invalidate_ui()
                    try:
                        await run_review(
                            client_cache, messages, cfg, registry, tracker, status=status
                        )
                    finally:
                        _ACTIVE_PROFILE = prev_active
                        _GLOBAL_MODEL = prev_model
                        invalidate_ui()
                    continue

                if cmd == "remember":
                    if not arg:
                        pt_print('Usage: /remember "text to remember"', "dim")
                        continue
                    from .tools.memory import MemoryTool
                    mem = MemoryTool()
                    result = await mem.run(action="store", text=arg)
                    pt_print(f"Memory: {result}", "green")
                    continue

                if cmd == "search":
                    if not arg:
                        pt_print("Usage: /search keyword", "dim")
                        continue
                    from .tools.memory import MemoryTool
                    mem = MemoryTool()
                    result = await mem.run(action="search", keyword=arg)
                    pt_print(result)
                    continue

                if cmd == "memory":
                    from .tools.memory import MemoryTool
                    mem = MemoryTool()
                    if arg:
                        result = await mem.run(action="read", topic=arg)
                    else:
                        result = await mem.run(action="list")
                    pt_print(result)
                    continue

                if cmd in ("bg", "background"):
                    if cmd == "bg" and arg:
                        bt = bg_mgr.add(arg[:60], _run_agent_background(active_client, system_prompt, arg, messages, tools, registry, cfg, bg_mgr, logger))
                        pt_print(f"Started background task: {bt.name}", "green")
                    else:
                        pt_print("\n")
                        pt_print("╔" + "═" * 50 + "╗", "blue")
                        pt_print("║" + " Background Tasks ".ljust(50) + "║", "blue")
                        pt_print("╠" + "═" * 50 + "╣")
                        for line_item in bg_mgr.status_panel().split("\n"):
                            pt_print("║" + line_item.ljust(50) + "║")
                        pt_print("╚" + "═" * 50 + "╝\n", "blue")
                    continue

                pt_print(f"Unknown command: {cmd}. Type /help for commands.", "red")
                continue

            # Normal prompt — run agent
            messages.append({"role": "user", "content": line})
            logger.log("user", line)

            async def cli_input_fn(prompt_text=""):
                # Pause the live status bar before prompting for input
                status.stop()
                try:
                    return await session.prompt_async(ANSI(f"\x1b[1;33m{prompt_text}\x1b[0m"))
                except (EOFError, KeyboardInterrupt):
                    return "n"
                finally:
                    # Resume the live status bar after input is received
                    status.start()

            agent_task = asyncio.create_task(
                run_turns(active_client, messages, tools, registry, cfg, status=status, tracker=tracker, input_fn=cli_input_fn, logger=logger)
            )

            try:
                await agent_task
            except (asyncio.CancelledError, KeyboardInterrupt):
                if agent_task and not agent_task.done():
                    agent_task.cancel()
                pt_print("Task cancelled.", "yellow")
            except Exception as e:
                pt_print(f"Agent error: {e}", "red")
            finally:
                tracker.session_input = await _total_message_tokens(messages, cfg)
                invalidate_ui()
                agent_task = None


    finally:
        if agent_task:
            agent_task.cancel()
        bg_check_done.set()
        bg_check_task.cancel()
        tracker.flush()
        bg_mgr.save()
        logger.close()


async def _bg_status_watcher(bg_mgr: BackgroundManager, done: asyncio.Event):
    """Periodically prints background task status updates."""
    while not done.is_set():
        try:
            await asyncio.sleep(5)
            tasks = bg_mgr.list_tasks()
            new_done = [t for t in tasks if t.done and not getattr(t, '_reported', False)]
            for t in new_done:
                t._reported = True
                if t.error:
                    pt_print(f"\nBackground task '{t.name}' failed: {t.error}", "dim")
                else:
                    pt_print(f"\nBackground task '{t.name}' completed.", "dim")
        except asyncio.CancelledError:
            break
