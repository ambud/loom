import json
import pytest
from src.agent import execute_tools
from src.tools.base import ToolRegistry, Tool

@pytest.mark.asyncio
async def test_execute_tools_repair_truncated_string():
    registry = ToolRegistry()
    cfg = {"approval_mode": "yolo"}
    
    # Truncated tool call: missing closing quote and brace
    tool_calls = [{
        "id": "call_1",
        "type": "function",
        "function": {
            "name": "read_file",
            "arguments": '{"path": "src/main.py'
        }
    }]
    
    # Mock the tool implementation
    class MockReadTool(Tool):
        name = "read_file"
        description = "read"
        async def run(self, **kwargs):
            return "file content"
            
    registry.register(MockReadTool())
    
    results, stop_loop = await execute_tools(tool_calls, registry, cfg)
    
    # Verify it was repaired and executed
    assert len(results) == 1
    assert results[0] == "file content"

@pytest.mark.asyncio
async def test_execute_tools_repair_complex_truncation():
    registry = ToolRegistry()
    cfg = {"approval_mode": "yolo"}
    
    # Truncated tool call with escaped quotes inside
    tool_calls = [{
        "id": "call_2",
        "type": "function",
        "function": {
            "name": "write_file",
            "arguments": '{"path": "test.py", "content": "print(\\"hello world'
        }
    }]
    
    class MockWriteTool(Tool):
        name = "write_file"
        description = "write"
        async def run(self, **kwargs):
            return f"wrote {kwargs.get('content')}"
            
    registry.register(MockWriteTool())
    
    results, stop_loop = await execute_tools(tool_calls, registry, cfg)
    
    assert len(results) == 1
    assert "wrote print(\"hello world" in results[0]
