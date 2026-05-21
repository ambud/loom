"""Tests for file_tools fixes."""

import asyncio
import os
import tempfile
import pytest

from src.tools.file_tools import ReadFileTool


@pytest.mark.asyncio
async def test_read_file_end_line_only():
    """Bug #8: end_line alone was silently ignored."""
    tool = ReadFileTool()
    content = "\n".join(f"line{i+1}" for i in range(20))
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(content)
        path = f.name
    try:
        result = await tool.run(filepath=path, end_line=5)
        # Should return lines 1-5, not all 20
        lines = [l for l in result.split("\n") if l.strip() and not l.strip().startswith("---")]
        assert len(lines) == 5, f"Expected 5 content lines, got {len(lines)}: {lines}"
        assert "line1" in result
        assert "line5" in result
        assert "line6" not in result
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_read_file_start_and_end():
    tool = ReadFileTool()
    content = "\n".join(f"line{i+1}" for i in range(20))
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(content)
        path = f.name
    try:
        result = await tool.run(filepath=path, start_line=5, end_line=10)
        lines = [l for l in result.split("\n") if l.strip() and not l.strip().startswith("---")]
        assert len(lines) == 6, f"Expected 6 content lines, got {len(lines)}"
        assert "line5" in result
        assert "line10" in result
        assert "line4" not in result
        assert "line11" not in result
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_read_file_start_line_only():
    tool = ReadFileTool()
    content = "\n".join(f"line{i+1}" for i in range(20))
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(content)
        path = f.name
    try:
        result = await tool.run(filepath=path, start_line=15)
        assert "line15" in result
        assert "line14" not in result
    finally:
        os.unlink(path)
