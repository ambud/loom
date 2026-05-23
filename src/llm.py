"""LLM integration: tool schemas and token counting."""

from __future__ import annotations

import httpx
from typing import Any

from .utils import pt_print

_TOKENIZE_AVAILABLE = True
_HTTP_CLIENT: httpx.AsyncClient | None = None


async def get_http_client() -> httpx.AsyncClient:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None or _HTTP_CLIENT.is_closed:
        _HTTP_CLIENT = httpx.AsyncClient(timeout=10.0)
    return _HTTP_CLIENT


async def close_http_client():
    global _HTTP_CLIENT
    if _HTTP_CLIENT is not None:
        await _HTTP_CLIENT.aclose()
        _HTTP_CLIENT = None


async def async_count_tokens(text: str, cfg: dict) -> int:
    """Call the llama.cpp /tokenize endpoint for exact token counts.
    If the endpoint fails once, it falls back to len(text) for subsequent calls.
    """
    global _TOKENIZE_AVAILABLE
    if not text:
        return 0

    if _TOKENIZE_AVAILABLE:
        base_url = cfg.get("base_url", "http://localhost:8080/v1")
        # llama.cpp server usually has /tokenize at the root, but base_url might include /v1
        # We strip /v1 if present to get the base server URL
        root_url = base_url.replace("/v1", "").rstrip("/")
        url = f"{root_url}/tokenize"

        try:
            client = await get_http_client()
            resp = await client.post(url, json={"content": text}, timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()
                # llama.cpp returns {"tokens": [id1, id2, ...]}
                return len(data.get("tokens", []))
            else:
                _TOKENIZE_AVAILABLE = False
        except Exception:
            _TOKENIZE_AVAILABLE = False

    # Fallback to character count as a safe upper bound for tokens
    return len(text)


TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command. Use for builds, tests, git, file inspection, package management, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The shell command to execute."},
                    "timeout": {"type": "integer", "description": "Optional timeout in seconds (default 120)."},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file. Optionally specify a line range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "Absolute or relative path to the file."},
                    "start_line": {"type": "integer", "description": "Optional 1-based start line."},
                    "end_line": {"type": "integer", "description": "Optional 1-based end line."},
                },
                "required": ["filepath"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file, creating it or overwriting it entirely.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "Path to the file."},
                    "content": {"type": "string", "description": "Full file content to write."},
                },
                "required": ["filepath", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": (
                "Perform an exact string replacement in a file. "
                "Find old_string and replace with new_string. "
                "old_string must appear exactly once in the file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "Path to the file."},
                    "old_string": {"type": "string", "description": "Exact text to find (must be unique in file, including whitespace, unless replace_all is true)."},
                    "new_string": {"type": "string", "description": "Replacement text."},
                    "replace_all": {"type": "boolean", "description": "If true, replace all occurrences of old_string instead of requiring uniqueness."},
                },
                "required": ["filepath", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob_search",
            "description": "Find files matching a glob pattern (e.g. 'src/**/*.py').",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern to match."},
                    "path": {"type": "string", "description": "Optional base directory to search in (default: '.')."},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep_search",
            "description": "Search file contents with regex. Supports glob filter and context lines.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern to search for."},
                    "path": {"type": "string", "description": "File or directory to search in (default: '.')."},
                    "glob": {"type": "string", "description": "Optional glob to filter files (e.g. '*.py')."},
                    "context": {"type": "integer", "description": "Number of context lines before/after each match."},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "Show git working tree status: branch, staged, unstaged, and untracked files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cwd": {"type": "string", "description": "Optional working directory (default: repo root)."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "Show git diff of working tree or staged changes. Use staged=true for staged changes only.",
            "parameters": {
                "type": "object",
                "properties": {
                    "staged": {"type": "boolean", "description": "If true, show staged diff (git diff --cached). Default false."},
                    "cwd": {"type": "string", "description": "Optional working directory (default: repo root)."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_commit",
            "description": "Stage all changes and commit with the given message. Automatically refuses to commit files that look like secrets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Commit message."},
                    "cwd": {"type": "string", "description": "Optional working directory (default: repo root)."},
                },
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_log",
            "description": "Show recent git commits with hash, subject, and date.",
            "parameters": {
                "type": "object",
                "properties": {
                    "count": {"type": "integer", "description": "Number of recent commits to show (default: 5)."},
                    "cwd": {"type": "string", "description": "Optional working directory (default: repo root)."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_bg",
            "description": "Start a shell command running in the background (non-blocking). Output streams live to the terminal. Returns a job_id immediately. Use bg_check to poll for final results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The shell command to execute in the background."},
                    "cwd": {"type": "string", "description": "Optional working directory (default: repo root)."},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bg_check",
            "description": "Check the status and collected output of a background job. If still running, shows output so far. If done, shows final output and removes the job.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "The job_id returned by run_bg."},
                },
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory",
            "description": "Store or search persistent cross-session memories. Action 'store': save a learning/fact. Action 'search': find memories by keyword. Action 'list': show all topics. Action 'read': read a specific topic file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["store", "search", "list", "read"],
                        "description": "Action: store, search, list, or read.",
                    },
                    "text": {
                        "type": "string",
                        "description": "Text to store (for action='store').",
                    },
                    "keyword": {
                        "type": "string",
                        "description": "Keyword to search for (for action='search').",
                    },
                    "topic": {
                        "type": "string",
                        "description": "Topic file name to read (for action='read').",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan",
            "description": "Propose a step-by-step plan for the current task. Use this before executing complex tool sequences to ensure the user approves the approach.",
            "parameters": {
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of steps in the plan.",
                    },
                    "rationale": {"type": "string", "description": "Technical rationale for this approach."},
                },
                "required": ["steps"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_task",
            "description": "Signal that the task is fully completed. You MUST provide a summary of what was accomplished and how it was verified. This is the only way to officially finish a task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Comprehensive summary of work done."},
                    "verification": {"type": "string", "description": "Description of how the work was verified (e.g. which tests were run)."},
                },
                "required": ["summary", "verification"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fail_task",
            "description": "Formally signal that you cannot complete the task. Use this when you hit a dead end, lack necessary information, or encounter an unrecoverable error. You must provide a clear reason and what you tried.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "Detailed explanation of why the task failed."},
                    "attempts": {"type": "string", "description": "Summary of what was attempted before giving up."},
                },
                "required": ["reason", "attempts"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_diff",
            "description": "Apply a unified diff (patch) to a file. This is more robust than edit_file for complex changes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "The path to the file to patch."},
                    "diff": {"type": "string", "description": "The unified diff content to apply."},
                },
                "required": ["filepath", "diff"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replace_lines",
            "description": "Replace a specific range of lines in a file. Lines are 1-indexed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "The path to the file."},
                    "start_line": {"type": "integer", "description": "The starting line number (inclusive)."},
                    "end_line": {"type": "integer", "description": "The ending line number (inclusive)."},
                    "new_content": {"type": "string", "description": "The new content to put in place of the lines."},
                },
                "required": ["filepath", "start_line", "end_line", "new_content"],
            },
        },
    },
]
