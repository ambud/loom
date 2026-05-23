"""File-based tools: read, write, edit."""

from __future__ import annotations

import asyncio
import os
from .base import Tool
from .safety import check_sensitive_access


class ReadFileTool(Tool):
    name = "read_file"
    description = "Read the contents of a file, optionally a line range."

    def _read(self, path: str, filepath: str, start_line: int | None, end_line: int | None) -> str:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except FileNotFoundError:
            return f"Error: file not found: {filepath}"
        except PermissionError:
            return f"Error: permission denied: {filepath}"

        if start_line is not None:
            lines = lines[start_line - 1:]
            base_offset = start_line - 1
        else:
            base_offset = 0

        if end_line is not None:
            abs_end = end_line - base_offset
            lines = lines[:abs_end]

        # Format with line numbers
        offset = base_offset
        result_lines = []
        for i, line in enumerate(lines, start=offset + 1):
            result_lines.append(f"{i:>6}  {line}")

        return (
            f"--- {filepath} ---\n"
            + "".join(result_lines)
            + f"\n--- end of {filepath} ({len(lines)} lines) ---"
        )

    async def run(
        self,
        filepath: str,
        start_line: int | None = None,
        end_line: int | None = None,
        **_,
    ) -> str:
        err = check_sensitive_access(filepath)
        if err:
            return err

        path = os.path.expanduser(filepath)
        if not os.path.isabs(path):
            path = os.path.join(os.getcwd(), path)

        return await asyncio.to_thread(self._read, path, filepath, start_line, end_line)


class WriteFileTool(Tool):
    name = "write_file"
    description = "Write content to a file, creating or overwriting it."

    def _write(self, path: str, filepath: str, content: str) -> str:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Written {len(content)} bytes to {filepath}"

    async def run(self, filepath: str, content: str, **_) -> str:
        path = os.path.expanduser(filepath)
        if not os.path.isabs(path):
            path = os.path.join(os.getcwd(), path)

        return await asyncio.to_thread(self._write, path, filepath, content)


class EditFileTool(Tool):
    name = "edit_file"
    description = (
        "Replace an exact string in a file with a new string. "
        "Use replace_all=true to replace all occurrences."
    )

    def _edit(self, path: str, filepath: str, old_string: str, new_string: str, replace_all: bool) -> str:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                source = f.read()
        except FileNotFoundError:
            return f"Error: file not found: {filepath}"

        count = source.count(old_string)
        if count == 0:
            return (
                f"Error: old_string not found in {filepath}. "
                "Make sure it matches exactly (including whitespace)."
            )
        if not replace_all and count > 1:
            return (
                f"Error: old_string appears {count} times in {filepath}. "
                "It must be unique. Provide more surrounding context or use replace_all=true."
            )

        occurrences = count
        source = source.replace(old_string, new_string) if replace_all else source.replace(old_string, new_string, 1)
        with open(path, "w", encoding="utf-8") as f:
            f.write(source)
        return f"Replaced {occurrences} occurrence(s) in {filepath}"

    async def run(self, filepath: str, old_string: str, new_string: str, replace_all: bool = False, **_) -> str:
        err = check_sensitive_access(filepath)
        if err:
            return err

        path = os.path.expanduser(filepath)
        if not os.path.isabs(path):
            path = os.path.join(os.getcwd(), path)

        return await asyncio.to_thread(self._edit, path, filepath, old_string, new_string, replace_all)


class ApplyDiffTool(Tool):
    name = "apply_diff"
    description = "Apply a unified diff (patch) to a file."

    def _apply(self, path: str, filepath: str, diff_text: str) -> str:
        try:
            import subprocess
            # We use the system 'patch' command for robustness
            proc = subprocess.Popen(
                ["patch", "-u", path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            out, err = proc.communicate(input=diff_text)
            if proc.returncode == 0:
                return f"Successfully applied diff to {filepath}\n{out}"
            else:
                return f"Error: patch failed on {filepath}\nSTDOUT: {out}\nSTDERR: {err}"
        except Exception as e:
            return f"Error: failed to apply diff: {e}"

    async def run(self, filepath: str, diff: str, **_) -> str:
        err = check_sensitive_access(filepath)
        if err:
            return err

        path = os.path.expanduser(filepath)
        if not os.path.isabs(path):
            path = os.path.join(os.getcwd(), path)

        return await asyncio.to_thread(self._apply, path, filepath, diff)


class ReplaceLinesTool(Tool):
    name = "replace_lines"
    description = "Replace a specific range of lines in a file (1-indexed)."

    def _replace(self, path: str, filepath: str, start: int, end: int, content: str) -> str:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except FileNotFoundError:
            return f"Error: file not found: {filepath}"

        if start < 1 or start > len(lines):
            return f"Error: start_line {start} out of range (1-{len(lines)})"
        
        # Adjust for 0-indexing
        s_idx = start - 1
        e_idx = min(len(lines), end)

        # Ensure new content ends with a newline if it's multiple lines
        if content and not content.endswith("\n"):
            content += "\n"

        lines[s_idx:e_idx] = [content]
        
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(lines)
        
        return f"Replaced lines {start}-{e_idx} in {filepath}"

    async def run(self, filepath: str, start_line: int, end_line: int, new_content: str, **_) -> str:
        err = check_sensitive_access(filepath)
        if err:
            return err

        path = os.path.expanduser(filepath)
        if not os.path.isabs(path):
            path = os.path.join(os.getcwd(), path)

        return await asyncio.to_thread(self._replace, path, filepath, start_line, end_line, new_content)
