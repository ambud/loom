"""Git tools: status, diff, commit, log."""

from __future__ import annotations

import asyncio
import os
from .base import Tool
from .safety import is_sensitive


# Files that must never be committed


class GitStatusTool(Tool):
    name = "git_status"
    description = (
        "Show the git working tree status: branch, staged files, "
        "unstaged changes, untracked files."
    )

    async def run(self, cwd: str | None = None, **_) -> str:
        cwd = cwd or os.environ.get("AGENT_CWD", os.getcwd())
        proc = await asyncio.create_subprocess_shell(
            "git status --porcelain && echo '---BRANCH---' && git branch --show-current",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        out = stdout.decode().strip()
        err = stderr.decode().strip()

        if proc.returncode != 0:
            return f"git error: {err}" if err else "Not a git repository"

        lines = out.split("\n")
        branch = "unknown"
        status_lines = []
        for line in lines:
            if line.startswith("---BRANCH---"):
                idx = lines.index(line)
                branch = lines[idx + 1].strip() if idx + 1 < len(lines) else "unknown"
            else:
                status_lines.append(line)

        staged = [l for l in status_lines if l.startswith(("M ", "A ", "R ", "C "))]
        unstaged = [l for l in status_lines if l and l[2:].strip() and not l.startswith(("M ", "A ", "R ", "C "))]
        untracked = [l for l in status_lines if l.startswith("??")]

        parts = [f"Branch: {branch}"]
        if staged:
            parts.append(f"Staged ({len(staged)}):")
            parts.extend(staged)
        if unstaged:
            parts.append(f"Unstaged ({len(unstaged)}):")
            parts.extend(unstaged)
        if untracked:
            parts.append(f"Untracked ({len(untracked)}):")
            parts.extend(untracked)
        if not staged and not unstaged and not untracked:
            parts.append("Working tree clean")

        return "\n".join(parts)


class GitDiffTool(Tool):
    name = "git_diff"
    description = (
        "Show git diff of working tree changes or staged changes. "
        "Set staged=true to see staged diffs."
    )

    async def run(self, staged: bool = False, cwd: str | None = None, **_) -> str:
        cwd = cwd or os.environ.get("AGENT_CWD", os.getcwd())
        cmd = "git diff --cached" if staged else "git diff"
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        out = stdout.decode().strip()

        if proc.returncode != 0:
            return f"git error: {stderr.decode().strip()}"

        if not out:
            return "No changes" + (" staged" if staged else "")

        if len(out) > 6000:
            out = out[:6000] + "\n... [truncated, total output too long]"

        return out


class GitCommitTool(Tool):
    name = "git_commit"
    description = (
        "Stage all changes and commit with the given message. "
        "Refuses to commit files that look like secrets."
    )

    async def run(self, message: str, cwd: str | None = None, **_) -> str:
        cwd = cwd or os.environ.get("AGENT_CWD", os.getcwd())

        # Get list of all changed files (staged + unstaged + untracked)
        status = await asyncio.create_subprocess_shell(
            "git status --porcelain",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, _ = await asyncio.wait_for(status.communicate(), timeout=15)
        status_lines = stdout.decode().strip().split("\n")

        # Extract file paths from status output (column 3+, spaces preserved)
        changed_files = []
        for line in status_lines:
            if len(line) >= 3:
                fp = line[3:]
                if fp:
                    changed_files.append(fp)

        # Check for secrets BEFORE staging anything
        for f in changed_files:
            if is_sensitive(f):
                return f"Refusing to commit: '{f}' looks like a sensitive file"

        if not changed_files:
            return "No changes to commit"

        # Stage only the changed files (not -A which could pick up unexpected paths)
        quoted_files = " ".join(shell_quote(f) for f in changed_files)
        proc = await asyncio.create_subprocess_shell(
            f'git add {quoted_files} && git commit -m {shell_quote(message)}',
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        out = stdout.decode().strip()
        err = stderr.decode().strip()

        if proc.returncode != 0:
            if "nothing added" in err or "no changes" in err:
                return "No changes to commit"
            return f"Commit failed: {err}"

        if "nothing to commit" in out:
            return "No changes to commit"

        # Extract commit hash
        for line in out.split("\n"):
            if "[main " in line or "[master " in line:
                return f"Committed: {line.strip()}"

        return "Committed: " + out[:200]


class GitLogTool(Tool):
    name = "git_log"
    description = "Show recent git commits (hash, subject, date)."

    async def run(self, count: int = 5, cwd: str | None = None, **_) -> str:
        cwd = cwd or os.environ.get("AGENT_CWD", os.getcwd())
        proc = await asyncio.create_subprocess_shell(
            f'git log --format="%h %s %ad" --date=short -n {count}',
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)

        if proc.returncode != 0:
            err = stderr.decode().strip()
            return f"git error: {err}" if err else "Not a git repository"

        out = stdout.decode().strip()
        if not out:
            return "No commits found"

        return out


def shell_quote(s: str) -> str:
    """Quote a string for safe use in shell commands."""
    return "'" + s.replace("'", "'\"'\"'") + "'"
