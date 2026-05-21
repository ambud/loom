# Custom Prompts

Loom comes with high-quality, pre-optimized system prompts for general development and code review. However, you can easily override them to match your own style or requirements.

## 1. System Prompt

The **System Prompt** defines Loom's identity, tone, and tool-use guidelines. It is the primary "instruction manual" the LLM follows.

### How to Override
Add the `system_prompt_file` key to your `~/.loom/config.yaml`:

```yaml
system_prompt_file: "~/my-prompts/custom-system.md"
```

### The Memory Index
Loom automatically appends a **Cross-Session Memory Index** to your system prompt. This index contains a list of all topics and facts you've asked Loom to remember using the `/remember` command. This ensures your custom prompt doesn't "break" the agent's long-term memory.

## 2. Review Prompt

The **Review Prompt** is used specifically when you trigger the `/review` command. It instructs the agent to focus on bugs, security, and conventions rather than general feature implementation.

### How to Override
Add the `review_system_prompt_file` key to your `~/.loom/config.yaml`:

```yaml
review_system_prompt_file: "~/my-prompts/custom-reviewer.md"
```

## Tips for Better Prompts

1.  **Be Explicit about Tools:** If you want the agent to use a specific pattern (like "always run tests after a write"), specify it in your custom system prompt.
2.  **Use Markdown:** Both Loom and the underlying LLMs (like Qwen and Llama) respond best to well-structured Markdown headers.
3.  **Local Context:** Since Loom runs locally, you can include specific details about your machine or local build tools without worrying about data leakage.
