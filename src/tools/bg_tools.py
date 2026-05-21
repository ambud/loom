"""Background shell tools: fire-and-forget commands with live output streaming."""

from __future__ import annotations

import asyncio
import collections
import os
import time
from .base import Tool

# Shared registry of running background tasks
_tasks: dict[str, dict] = {}
_job_counter = 0


def _make_job_id() -> str:
    """Create a unique job id that avoids collisions."""
    global _job_counter
    _job_counter += 1
    ts = int(time.time())
    return f"{ts}-{_job_counter:05d}"


class RunBgTool(Tool):
    name = "run_bg"
    description = (
        "Start a shell command running in the background (non-blocking). "
        "Output streams live to the terminal. Returns a job_id immediately. "
        "Use bg_check to poll for final status and collected output."
    )

    async def run(self, command: str, cwd: str | None = None, **_) -> str:
        cwd = cwd or os.environ.get("AGENT_CWD", os.getcwd())
        job_id = _make_job_id()

        proc = await asyncio.create_subprocess_shell(
            command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )

        stdout_buf = collections.deque(maxlen=200)
        stderr_buf = collections.deque(maxlen=100)

        # Spawn reader tasks that stream output live
        out_task = asyncio.create_task(
            _read_lines(proc.stdout, stdout_buf, job_id)
        )
        err_task = asyncio.create_task(
            _read_lines(proc.stderr, stderr_buf, job_id)
        )

        _tasks[job_id] = {
            "pid": proc.pid,
            "command": command,
            "proc": proc,
            "started": time.time(),
            "done": False,
            "stdout": stdout_buf,
            "stderr": stderr_buf,
            "returncode": None,
            "_out_task": out_task,
            "_err_task": err_task,
        }

        return f"Started (job={job_id}, pid={proc.pid}): {command}"


class BgCheckTool(Tool):
    name = "bg_check"
    description = (
        "Check the status and collected output of a background job. "
        "If the job is still running, returns status and output so far. "
        "If done, returns final output."
    )

    async def run(self, job_id: str, **_) -> str:
        task = _tasks.get(job_id)
        if not task:
            return f"Unknown job: {job_id}"

        proc = task["proc"]
        elapsed = time.time() - task["started"]

        # Check if process has finished
        if proc.returncode is not None and not task["done"]:
            task["done"] = True
            task["returncode"] = proc.returncode

            # Wait briefly for remaining buffered output
            await asyncio.sleep(0.1)

            # Cancel reader tasks that may still be hanging on the pipe
            for t in (task["_out_task"], task["_err_task"]):
                if not t.done():
                    t.cancel()

        out_lines = list(task["stdout"])
        err_lines = list(task["stderr"])

        if task["done"]:
            header = (
                f"Job {job_id}: completed (exit={proc.returncode}, took {elapsed:.1f}s)\n"
                f"Command: {task['command']}\n"
                f"PID: {task['pid']}"
            )
            body = [header]
            if out_lines:
                body.append("--- STDOUT ---")
                body.extend(out_lines[-200:])
            if err_lines:
                body.append("--- STDERR ---")
                body.extend(err_lines[-100:])
            # Clean up completed job from memory
            del _tasks[job_id]
            return "\n".join(body)

        # Still running — show what we have so far
        header = f"Job {job_id}: running (elapsed {elapsed:.1f}s, pid={task['pid']}): {task['command']}"
        body = [header]
        if out_lines:
            body.append("--- STDOUT so far ---")
            body.extend(out_lines[-50:])
        if err_lines:
            body.append("--- STDERR so far ---")
            body.extend(err_lines[-50:])
        return "\n".join(body)


def list_bg_jobs() -> str:
    """List all background jobs for /bg-status slash command."""
    if not _tasks:
        return "No background jobs."
    lines = []
    for jid, t in _tasks.items():
        if t["done"]:
            status = f"done (exit={t['returncode']})"
        elif t["proc"].returncode is not None:
            status = "finished (uncollected)"
        else:
            status = "running"
        elapsed = time.time() - t["started"]
        lines.append(f"  {jid}: [{status}] {t['command'][:60]} ({elapsed:.0f}s)")
    return "\n".join(lines)


async def _read_lines(pipe, buf: collections.deque, job_id: str) -> None:
    """Read lines from a process pipe and print them live."""
    import sys
    try:
        async for line in pipe:
            text = line.decode(errors="replace").rstrip("\n\r")
            if text:
                buf.append(text)
                sys.stderr.write(f"[{job_id}] {text}\n")
                sys.stderr.flush()
    except asyncio.CancelledError:
        pass
