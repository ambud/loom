"""Bash tool: execute shell commands."""

from __future__ import annotations

import asyncio
import os
from .base import Tool


class BashTool(Tool):
    name = "bash"
    description = (
        "Run a shell command and return its output. "
        "Use for builds, tests, git, file inspection, etc."
    )

    async def run(self, command: str, timeout: int = 120, **_) -> str:
        cwd = os.environ.get("AGENT_CWD", os.getcwd())
        proc = await asyncio.create_subprocess_shell(
            command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            return f"Command timed out after {timeout}s: {command}"

        parts = []
        out = stdout.decode()
        err = stderr.decode()
        if out:
            parts.append(f"STDOUT:\n{out}")
        if err:
            parts.append(f"STDERR:\n{err}")
        if not parts:
            parts.append("(no output)")
        return f"exit={proc.returncode}\n" + "\n".join(parts)
