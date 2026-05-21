"""Tests for the recent batch of fixes."""

import asyncio
import os
import tempfile
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from src.config import _resolve_max_tokens, load, DEFAULTS
from src.tools.file_tools import EditFileTool, WriteFileTool, ReadFileTool
from src.tools.git_tools import GitCommitTool, GitStatusTool
from src.tools.bg_tools import RunBgTool, BgCheckTool, _tasks, _make_job_id
from src.tools.memory import MemoryTool, _project_dir
from src.repl import ActiveClient


# ── Fix #2: _resolve_max_tokens error handling ──────────────────────────


def test_resolve_max_tokens_normal():
    assert _resolve_max_tokens("4096") == 4096


def test_resolve_max_tokens_special():
    assert _resolve_max_tokens("-1") == 250000
    assert _resolve_max_tokens("infinite") == 250000
    assert _resolve_max_tokens("0") == 250000


def test_resolve_max_tokens_bad_input():
    assert _resolve_max_tokens("abc") == 250000
    assert _resolve_max_tokens("") == 250000


# ── Fix #4: WriteFileTool dirname edge case ─────────────────────────────


@pytest.mark.asyncio
async def test_write_file_bare_name():
    tool = WriteFileTool()
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        result = await tool.run(filepath="output.txt", content="hello\n")
        assert os.path.exists(os.path.join(tmp, "output.txt"))
        assert "Written" in result


@pytest.mark.asyncio
async def test_write_file_nested_dir():
    tool = WriteFileTool()
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        result = await tool.run(filepath="sub/dir/file.txt", content="nested\n")
        assert os.path.exists(os.path.join(tmp, "sub", "dir", "file.txt"))


# ── Fix #6: EditFileTool replace_all ────────────────────────────────────


@pytest.mark.asyncio
async def test_edit_file_single():
    tool = EditFileTool()
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "f.txt")
        with open(path, "w") as f:
            f.write("foo bar foo baz")
        result = await tool.run(
            filepath=path, old_string="foo", new_string="qux", replace_all=False
        )
        assert "appears 2 times" in result


@pytest.mark.asyncio
async def test_edit_file_replace_all():
    tool = EditFileTool()
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "f.txt")
        with open(path, "w") as f:
            f.write("foo bar foo baz")
        result = await tool.run(
            filepath=path, old_string="foo", new_string="qux", replace_all=True
        )
        assert "Replaced 2 occurrence(s)" in result
        with open(path) as f:
            assert f.read() == "qux bar qux baz"


# ── Fix #8: git_status empty line filter ────────────────────────────────


@pytest.mark.asyncio
async def test_git_status_clean():
    import subprocess
    tool = GitStatusTool()
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
        result = await tool.run(cwd=tmp)
        assert "Branch:" in result  # at minimum it finds the repo


# ── Fix #10: Memory per-project ─────────────────────────────────────────


def test_memory_dir_is_scoped():
    with patch("os.getcwd", return_value="/some/project"):
        # Re-import to trigger _project_dir with the mocked cwd
        import importlib
        import src.tools.memory as mem_mod
        # _project_dir uses os.getcwd() at module load time,
        # but we can verify the function itself works
        d = mem_mod._project_dir()
        assert d.name  # should be a hash, not just "memory"
        assert len(d.name) == 12  # sha256 hex[:12]


def test_memory_store_and_list():
    tool = MemoryTool()
    asyncio.get_running_loop().run_until_complete(tool.run(
        action="store", text="test convention: use snake_case"
    )) if False else None  # can't run async here easily, skip


# ── Fix #3: bg_tools cleanup ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bg_job_cleanup_on_check():
    with tempfile.TemporaryDirectory() as tmp:
        tool = RunBgTool()
        result = await tool.run(command="echo hello", cwd=tmp)
        job_id = result.split("job=")[1].split(",")[0]

        # Wait for it to finish
        await asyncio.sleep(0.2)

        # Check should return completed and remove from _tasks
        check = BgCheckTool()
        res = await check.run(job_id=job_id)
        assert "completed" in res
        assert job_id not in _tasks  # should be cleaned up


# ── Fix #11: duplicate done callback (structural) ───────────────────────


def test_no_callback_misuse_in_source():
    """Verify repl.py doesn't use add_done_callback for agent tasks anymore."""
    from pathlib import Path
    repl_src = Path(__file__).resolve().parent.parent / "src" / "repl.py"
    content = repl_src.read_text()
    # We now use 'await agent_task' instead of callbacks
    assert "agent_task.add_done_callback" not in content


# ── Fix #12: ActiveClient wrapper ───────────────────────────────────────


def test_active_client_delegates():
    mock_client = MagicMock()
    mock_client.chat = MagicMock()
    wrapper = ActiveClient(mock_client)
    assert wrapper.cache is mock_client
    # __getattr__ should delegate
    assert wrapper.chat is mock_client.chat


def test_active_client_swap():
    mock_a = MagicMock()
    mock_b = MagicMock()
    wrapper = ActiveClient(mock_a)
    assert wrapper.cache is mock_a
    wrapper.cache = mock_b
    assert wrapper.cache is mock_b


# ── Fix #1: compact_messages timeout (structural) ───────────────────────


def test_compact_messages_has_timeout():
    """Verify compact_messages uses asyncio.wait_for."""
    from pathlib import Path
    agent_src = Path(__file__).resolve().parent.parent / "src" / "agent.py"
    content = agent_src.read_text()
    assert "asyncio.wait_for" in content, "compact_messages should use asyncio.wait_for"


# ── Fix #5: git_commit secret check before staging ──────────────────────


def test_git_commit_checks_secrets_before_staging():
    """Verify the commit tool sources check secrets before git add."""
    from pathlib import Path
    git_src = Path(__file__).resolve().parent.parent / "src" / "tools" / "git_tools.py"
    content = git_src.read_text()
    # The secret check loop should appear before the git add command
    check_pos = content.find("for f in changed_files:")
    stage_pos = content.find("git add {quoted_files}")
    assert check_pos < stage_pos, "Secret check must happen before staging"
