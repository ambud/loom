# Context Compaction

Loom is designed to handle extremely long coding sessions by automatically managing the LLM's context window. Instead of crashing or losing data when a conversation gets too long, Loom uses a sophisticated **Compaction** strategy.

## How it Works

Loom monitors your token usage in real-time. When the conversation exceeds a specific threshold (default: **80%** of your `context_window`), the compaction process is triggered.

### The "Head-Tail" Strategy

Loom doesn't just truncate the oldest messages. It uses a strategy that preserves the most important context:

1.  **The Head (Preserved):** The **System Prompt** and any critical initialization context are always kept at the very beginning.
2.  **The Middle (Summarized):** Loom takes the bulk of the middle conversation—previous tasks, tool outputs, and discussions—and sends them to the LLM with a specialized "summarization" instruction. The LLM converts thousands of tokens of history into a concise list of "Core Progress & State."
3.  **The Tail (Preserved):** The most recent **N turns** (default: 3) are kept exactly as they are. This ensures the agent maintains "short-term memory" of exactly what was just discussed and what the immediate next steps are.

## Configuration

You can tune the compaction behavior in your `~/.loom/config.yaml`:

```yaml
# When to compact (80% of window)
compaction_threshold: 0.80

# How many recent turns to keep exactly as-is
compaction_keep_last_turns: 3

# Total window size to manage
context_window: 262000
```

## Why This Matters

*   **Reliability:** You never have to worry about the agent "forgetting" the system instructions or the immediate task at hand.
*   **Performance:** By keeping the context slim, LLM inference remains fast and efficient.
*   **Cost/Resource Efficiency:** If using a remote provider, this saves significant token costs. If local, it keeps VRAM usage stable.

You can manually trigger a compaction at any time using the `/compact` command.
