"""Search tools: glob and grep."""

from __future__ import annotations

import asyncio
import fnmatch
import os
import re
from .base import Tool
from .safety import is_sensitive


class GlobSearchTool(Tool):
    name = "glob_search"
    description = "Find files matching a glob pattern (e.g. '**/*.py')."

    def _do_search(self, pattern: str, base: str) -> list[str]:
        matches: list[str] = []
        for root, dirs, files in os.walk(base):
            # Skip hidden dirs and common non-source dirs
            dirs[:] = [
                d
                for d in dirs
                if not d.startswith(".") and d not in (
                    "node_modules", "__pycache__", "target", "build", "dist", "venv", ".venv", "env"
                )
            ]
            for name in files:
                rel = os.path.relpath(os.path.join(root, name), base)
                if is_sensitive(rel):
                    continue
                if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(name, pattern):
                    matches.append(rel)
        return matches

    async def run(self, pattern: str, path: str = ".", **_) -> str:
        base = os.path.expanduser(path)
        if not os.path.isabs(base):
            base = os.path.join(os.getcwd(), base)

        matches = await asyncio.to_thread(self._do_search, pattern, base)

        matches.sort()
        if not matches:
            return f"No files matching '{pattern}' in {path}"
        result = "\n".join(matches)
        return f"Found {len(matches)} file(s) matching '{pattern}':\n{result}"


class GrepSearchTool(Tool):
    name = "grep_search"
    description = "Search file contents with regex. Returns matching lines."

    def _do_grep(self, regex: re.Pattern, base: str, glob: str | None, context: int) -> tuple[list[str], int]:
        matches: list[str] = []
        total = 0

        files_to_search: list[str] = []
        if os.path.isfile(base):
            files_to_search = [base]
        else:
            for root, dirs, files in os.walk(base):
                dirs[:] = [
                    d
                    for d in dirs
                    if not d.startswith(".") and d not in (
                        "node_modules", "__pycache__", "target", "build", "dist", "venv", ".venv", "env"
                    )
                ]
                for name in files:
                    fp = os.path.join(root, name)
                    rel = os.path.relpath(fp, base)
                    if is_sensitive(rel):
                        continue
                    if glob and not fnmatch.fnmatch(name, glob):
                        continue
                    files_to_search.append(fp)

        for fp in files_to_search:
            try:
                with open(fp, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
            except (PermissionError, OSError):
                continue

            for i, line in enumerate(lines):
                if regex.search(line):
                    start = max(0, i - context)
                    end = min(len(lines), i + context + 1)
                    rel = os.path.relpath(fp, base)
                    for j in range(start, end):
                        marker = " >> " if j == i else "    "
                        matches.append(f"{rel}:{j + 1}:{marker}{lines[j]}")
                    if context > 0:
                        matches.append("-----\n")
                    total += 1
        return matches, total

    async def run(
        self,
        pattern: str,
        path: str = ".",
        glob: str | None = None,
        context: int = 0,
        **_,
    ) -> str:
        base = os.path.expanduser(path)
        if not os.path.isabs(base):
            base = os.path.join(os.getcwd(), base)

        regex = re.compile(pattern)
        matches, total = await asyncio.to_thread(self._do_grep, regex, base, glob, context)

        if not matches:
            return f"No matches for '{pattern}' in {path}"
        result = "\n".join(matches)
        return f"Found {total} match(es) for '{pattern}':\n{result}"
