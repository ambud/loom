import pytest
import os
import asyncio
from src.tools.file_tools import ReadFileTool, EditFileTool
from src.tools.search_tools import GlobSearchTool, GrepSearchTool
from src.tools.git_tools import GitCommitTool
from src.tools.safety import is_sensitive

def test_is_sensitive():
    assert is_sensitive("id_rsa") is True
    assert is_sensitive(".env") is True
    assert is_sensitive("src/main.py") is False
    assert is_sensitive("secrets/token.json") is True
    assert is_sensitive("path/to/my.key") is True

# Store the root CWD at module level to ensure we have a valid fallback
ROOT_CWD = os.path.abspath(os.getcwd())

@pytest.fixture(autouse=True)
def preserve_cwd():
    """Fixture to ensure CWD is restored after each test."""
    try:
        old_cwd = os.getcwd()
    except FileNotFoundError:
        old_cwd = ROOT_CWD
        
    yield
    
    try:
        os.chdir(old_cwd)
    except FileNotFoundError:
        os.chdir(ROOT_CWD)

@pytest.mark.asyncio
async def test_read_file_safety(tmp_path):
    (tmp_path / "safe.txt").write_text("safe content")
    tool = ReadFileTool()
    # Test blocked access
    result = await tool.run(filepath="id_rsa")
    assert "Safety Error" in result
    
    # Test allowed access
    os.chdir(tmp_path)
    result = await tool.run(filepath="safe.txt")
    assert "safe content" in result

@pytest.mark.asyncio
async def test_glob_search_safety(tmp_path):
    # Create a mix of sensitive and non-sensitive files
    (tmp_path / "safe.txt").write_text("safe")
    (tmp_path / "id_rsa").write_text("sensitive")
    
    tool = GlobSearchTool()
    # os.walk is used, so we need to be in the directory or pass it
    os.chdir(tmp_path)
    result = await tool.run(pattern="*")
    assert "safe.txt" in result
    assert "id_rsa" not in result

@pytest.mark.asyncio
async def test_grep_search_safety(tmp_path):
    (tmp_path / "safe.txt").write_text("secret_here")
    (tmp_path / "id_rsa").write_text("secret_here")
    
    tool = GrepSearchTool()
    os.chdir(tmp_path)
    result = await tool.run(pattern="secret_here")
    assert "safe.txt" in result
    assert "id_rsa" not in result

@pytest.mark.asyncio
async def test_git_commit_safety(tmp_path):
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=tmp_path)
    (tmp_path / ".env").write_text("SECRET=123")
    
    tool = GitCommitTool()
    # We need to be in the repo
    os.chdir(tmp_path)
    result = await tool.run(message="commit secrets", cwd=str(tmp_path))
    assert "Refusing to commit" in result
    assert ".env" in result
