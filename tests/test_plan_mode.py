import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from src.agent import run_turns

@pytest.mark.asyncio
async def test_run_turns_with_plan_tool_approved():
    # Mock client and response that triggers the 'plan' tool
    client = MagicMock()
    
    # First call returns the plan tool
    mock_response_1 = MagicMock()
    mock_response_1.model_dump.return_value = {
        "choices": [{"message": {"content": "I will propose a plan.", "tool_calls": [
            {"id": "call_123", "type": "function", "function": {"name": "plan", "arguments": '{"steps": ["Step 1"]}'}}
        ]}}]
    }
    # Second call returns no tool calls to break the loop
    mock_response_2 = MagicMock()
    mock_response_2.model_dump.return_value = {"choices": [{"message": {"content": "Execution done.", "tool_calls": []}}]}
    
    client.chat.completions.create = AsyncMock(side_effect=[mock_response_1, mock_response_2])

    messages = [{"role": "user", "content": "Build a house."}]
    cfg = {
        "plan": True, "max_tool_rounds": 5, "stream_text": False, "model": "llama",
        "max_tokens": 100, "temperature": 0.0, "context_window": 250000, "compaction_threshold": 0.8
    }
    
    # Mock input() to return 'y' (approve)
    with patch("builtins.input", return_value="y") as mock_input, \
         patch("src.agent._stream_response", side_effect=[
             ("I will propose a plan.", [{"id": "call_123", "type": "function", "function": {"name": "plan", "arguments": '{"steps": ["Step 1"]}'}}]),
             ("Execution done.", [])
         ]):
        
        await run_turns(client, messages, [], MagicMock(), cfg)
        
        assert "[PLAN MODE ENABLED]" in messages[0]["content"]
        mock_input.assert_called_once()
        assert any(m["role"] == "tool" and "Plan approved" in m["content"] for m in messages)

@pytest.mark.asyncio
async def test_run_turns_with_plan_tool_commented():
    client = MagicMock()
    
    mock_response_1 = MagicMock()
    mock_response_1.model_dump.return_value = {
        "choices": [{"message": {"content": "Planning...", "tool_calls": [
            {"id": "call_123", "type": "function", "function": {"name": "plan", "arguments": '{"steps": ["Step 1"]}'}}
        ]}}]
    }
    mock_response_2 = MagicMock()
    mock_response_2.model_dump.return_value = {"choices": [{"message": {"content": "Revised plan done.", "tool_calls": []}}]}
    
    client.chat.completions.create = AsyncMock(side_effect=[mock_response_1, mock_response_2])

    messages = [{"role": "user", "content": "Task"}]
    cfg = {
        "plan": True, "max_tool_rounds": 5, "stream_text": False, "model": "llama",
        "max_tokens": 100, "temperature": 0.0, "context_window": 250000, "compaction_threshold": 0.8
    }
    
    # Mock input() to return 'c' (comment) then the comment itself
    with patch("builtins.input", side_effect=["c", "Add more details", "y"]) as mock_input, \
         patch("src.agent._stream_response", side_effect=[
             ("Planning...", [{"id": "call_123", "type": "function", "function": {"name": "plan", "arguments": '{"steps": ["Step 1"]}'}}]),
             ("Revised plan done.", [])
         ]):
        
        await run_turns(client, messages, [], MagicMock(), cfg)
        
        assert any(m["role"] == "tool" and "User feedback: Add more details" in m["content"] for m in messages)
