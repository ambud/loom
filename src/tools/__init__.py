"""Tool package."""

from .base import Tool, ToolRegistry
from .bash_tool import BashTool
from .file_tools import ReadFileTool, WriteFileTool, EditFileTool
from .search_tools import GlobSearchTool, GrepSearchTool
from .git_tools import GitStatusTool, GitDiffTool, GitCommitTool, GitLogTool
from .bg_tools import RunBgTool, BgCheckTool
from .memory import MemoryTool, load_memory_index


def create_registry() -> ToolRegistry:
    """Create and populate a tool registry with all built-in tools."""
    reg = ToolRegistry()
    for cls in (
        BashTool, ReadFileTool, WriteFileTool, EditFileTool,
        GlobSearchTool, GrepSearchTool,
        GitStatusTool, GitDiffTool, GitCommitTool, GitLogTool,
        RunBgTool, BgCheckTool,
        MemoryTool,
    ):
        reg.register(cls())
    return reg
