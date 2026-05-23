import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from src.agent import _message_tokens, _total_message_tokens
from src.session import TokenTracker

@pytest.mark.asyncio
async def test_message_tokens_caching():
    """Verify that _message_tokens caches the result and doesn't call tokenize twice for same content."""
    cfg = {"base_url": "http://mock"}
    msg = {"role": "user", "content": "hello world"}
    
    with patch("src.agent.async_count_tokens", new_callable=AsyncMock) as mock_count:
        mock_count.return_value = 5
        
        # First call should call the actual tokenizer
        count1 = await _message_tokens(msg, cfg)
        assert count1 == 5
        assert mock_count.call_count == 1
        assert msg["_tokens"] == 5
        assert "_tokens_sig" in msg

        # Second call should use cache
        count2 = await _message_tokens(msg, cfg)
        assert count2 == 5
        assert mock_count.call_count == 1
  # Still 1

@pytest.mark.asyncio
async def test_message_tokens_cache_invalidation():
    """Verify that changing content invalidates the cache."""
    cfg = {"base_url": "http://mock"}
    msg = {"role": "user", "content": "hello"}
    
    with patch("src.agent.async_count_tokens", new_callable=AsyncMock) as mock_count:
        mock_count.side_effect = [2, 3]
        
        await _message_tokens(msg, cfg)
        assert mock_count.call_count == 1
        
        # Change content
        msg["content"] = "hello world"
        await _message_tokens(msg, cfg)
        assert mock_count.call_count == 2
        assert msg["_tokens"] == 3

@pytest.mark.asyncio
async def test_token_tracker_decoupled_add():
    """Verify TokenTracker.add updates totals but not current_tokens."""
    tracker = TokenTracker()
    tracker.current_tokens = 100
    
    tracker.add(50, is_output=False)
    assert tracker.session_input == 50
    assert tracker.session_total == 50
    assert tracker.current_tokens == 100  # Remains 100
    
    tracker.update_current(150)
    assert tracker.current_tokens == 150

@pytest.mark.asyncio
async def test_compact_messages_logic():
    """Verify that compact_messages summarizes and updates the tracker."""
    cfg = {
        "context_window": 1000,
        "compaction_threshold": 0.5,
        "compaction_keep_last_turns": 1,
        "model": "test-model"
    }
    # Need system + at least 3 messages for (keep_messages + 2) check
    # keep_messages = 1 + (1 * 2) = 3. 3 + 2 = 5.
    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "turn 1"},
        {"role": "assistant", "content": "response 1"},
        {"role": "user", "content": "turn 2"},
        {"role": "assistant", "content": "response 2"},
        {"role": "user", "content": "turn 3"}
    ]
    
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content="Summary of turns"))]
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
    
    tracker = TokenTracker()
    # Mock _total_message_tokens: 
    # 1. First call inside compact_messages (initial check): 600
    # 2. Second call inside compact_messages (after compaction): 200
    with patch("src.agent._total_message_tokens", side_effect=[600, 200]):
        from src.agent import compact_messages
        await compact_messages(mock_client, messages, cfg, force=True, tracker=tracker, status=None)
        
        # keep_messages = 3 (system + last turn). 
        # summarize_end = 6 - 3 = 3.
        # to_summarize = messages[1:3] (turn 1 user + turn 1 assistant).
        # new_messages = [system, summary, messages[3:]]
        # len(messages[3:]) = 3. 1 + 1 + 3 = 5.
        assert len(messages) == 5
        assert messages[1]["role"] == "system"
        assert "Summary" in messages[1]["content"]
        assert tracker.current_tokens == 200
