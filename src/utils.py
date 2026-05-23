from __future__ import annotations

import contextvars
import os
import sys
from io import StringIO
from typing import Any

from rich.console import Console
from rich.theme import Theme
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text as RichText
from prompt_toolkit import print_formatted_text, ANSI

# Define a consistent theme for the CLI
LOOM_THEME = Theme({
    "info": "dim cyan",
    "warning": "yellow",
    "error": "bold red",
    "success": "bold green",
    "tool": "bold blue",
    "bash": "bold green",
    "file": "bold magenta",
    "compact": "dim italic",
})

# Reusable buffer for capturing rich formatting
_buffer = StringIO()

# Capture console: used to generate ANSI strings from Rich components
_capture_console = Console(
    theme=LOOM_THEME, 
    file=_buffer, 
    force_terminal=True, 
    color_system="truecolor",
    highlight=False,
    width=100
)

# Standard console for all CLI output. 
# This console will be automatically patched by prompt-toolkit's patch_stdout()
# and will be used by Rich.Live when the status bar is active.
_cli_console = Console(theme=LOOM_THEME)

def _get_rendered() -> str:
    """Extract and clear the capture buffer."""
    val = _buffer.getvalue()
    _buffer.truncate(0)
    _buffer.seek(0)
    return val

# Context-local redirect for web/other interfaces
_print_redirect_cv = contextvars.ContextVar("print_redirect", default=None)

# Optional active live display for persistent CLI status bars
_active_live = None

def set_active_live(live):
    """Register the active Rich Live display to ensure all printing routes through it."""
    global _active_live
    _active_live = live

def set_print_redirect(fn):
    """Set a task-local redirection for pt_print and related functions."""
    return _print_redirect_cv.set(fn)

def _to_web_safe(text: str) -> str:
    """Convert newlines for terminal emulators like xterm.js."""
    if not text:
        return text
    return text.replace("\r\n", "\n").replace("\n", "\r\n")

def stream_print(text: str) -> None:
    """Efficiently stream text directly to stdout."""
    if not text:
        return
        
    redirect = _print_redirect_cv.get()
    if redirect:
        redirect(_to_web_safe(text))
    else:
        sys.stdout.write(text)
        sys.stdout.flush()


def pt_print(text: Any = "", style: str = "", markup: bool = True, highlight: bool = False, end: str = "\n") -> None:
    """The primary printing function for Loom. Handles CLI (Rich/Live) and Web UI (Redirects)."""
    redirect = _print_redirect_cv.get()
    
    # Fast path for empty lines
    if not text and not style:
        if redirect:
            redirect(_to_web_safe(end))
        elif _active_live:
            _active_live.console.print(end=end)
        else:
            _cli_console.print(end=end)
        return
    
    # 1. Always capture the Rich output to an ANSI string first.
    # This ensures that 'style', 'markup', etc. are handled identically everywhere.
    _capture_console.print(text, style=style, markup=markup, highlight=highlight, end="")
    rendered = _get_rendered()
    
    # 2. Route the rendered ANSI string to the correct interface
    if redirect:
        redirect(_to_web_safe(rendered + end))
    elif _active_live:
        # If a Rich Live display is active, we MUST print through its console
        # to ensure the status bar stays pinned at the bottom.
        _active_live.console.print(RichText.from_ansi(rendered), end=end)
    else:
        # Standard CLI output: Use the shared CLI console which handles ANSI perfectly.
        _cli_console.print(RichText.from_ansi(rendered), end=end)

def print_markdown(text: str, end: str = "\n") -> None:
    """Print markdown formatted text."""
    redirect = _print_redirect_cv.get()
    _capture_console.print(Markdown(text), end="")
    rendered = _get_rendered()
    
    if redirect:
        redirect(_to_web_safe(rendered + end))
    elif _active_live:
        _active_live.console.print(RichText.from_ansi(rendered), end=end)
    else:
        _cli_console.print(RichText.from_ansi(rendered), end=end)

def print_panel(text: Any, title: str | None = None, style: str = "info", end: str = "\n") -> None:
    """Print panel formatted text."""
    redirect = _print_redirect_cv.get()
    _capture_console.print(Panel(text, title=title, border_style=style, padding=(0, 1)), end="")
    rendered = _get_rendered()
    
    if redirect:
        redirect(_to_web_safe(rendered + end))
    elif _active_live:
        _active_live.console.print(RichText.from_ansi(rendered), end=end)
    else:
        _cli_console.print(RichText.from_ansi(rendered), end=end)
