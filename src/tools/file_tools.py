"""File-based tools: read, write, edit."""

from __future__ import annotations

import os
from .base import Tool


class ReadFileTool(Tool):
    name = "read_file"
    description = "Read the contents of a file, optionally a line range."

    async def run(
        self,
        filepath: str,
        start_line: int | None = None,
        end_line: int | None = None,
        **_,
    ) -> str:
        path = os.path.expanduser(filepath)
        if not os.path.isabs(path):
            path = os.path.join(os.getcwd(), path)

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
        elif end_line is not None:
            base_offset = 0
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


class WriteFileTool(Tool):
    name = "write_file"
    description = "Write content to a file, creating or overwriting it."

    async def run(self, filepath: str, content: str, **_) -> str:
        path = os.path.expanduser(filepath)
        if not os.path.isabs(path):
            path = os.path.join(os.getcwd(), path)

        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Written {len(content)} bytes to {filepath}"


class EditFileTool(Tool):
    name = "edit_file"
    description = (
        "Replace an exact string in a file with a new string. "
        "Use replace_all=true to replace all occurrences."
    )

    async def run(self, filepath: str, old_string: str, new_string: str, replace_all: bool = False, **_) -> str:
        path = os.path.expanduser(filepath)
        if not os.path.isabs(path):
            path = os.path.join(os.getcwd(), path)

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
