# Usage Guide: Commands & UX

Loom provides a dual-interface experience: a high-power CLI for terminal purists and a modern Web UI for visual multitasking. Both interfaces support the same core commands and interaction model.

## 🤖 Interaction Model: "Ask, Plan, Act"

Loom follows a structured workflow to ensure safety and precision:

1.  **Ask:** You provide a high-level instruction (e.g., "Refactor the database connection to use a pool").
2.  **Plan:** If **Plan Mode** is enabled, the agent will call the `plan` tool. You must review the steps and approve them before the agent proceeds.
3.  **Act:** The agent uses its tool suite (read, write, edit, shell) to execute the plan.

## ⌨️ Global Commands

Slash commands work in both the CLI and the Web UI.

### Conversation
*   `/help`: Show available commands and tool status.
*   `/plan`: Toggle **Mandatory Plan Mode**. When on, Loom *must* get your approval on a plan before taking action.
*   `/compact`: Manually trigger context compaction to clear up memory.
*   `/system`: Display the current system prompt.
*   `/reload`: Reload the system prompt from disk (useful after editing `system.md`).

### Knowledge & Memory
*   `/remember <fact>`: Store a specific learning or convention in the project's long-term memory.
*   `/search <keyword>`: Search through all stored memories for the current project.
*   `/memory [topic]`: List all memory topics, or read a specific topic file if a name is provided.

### Models & Profiles
*   `/model`: List all configured model profiles and see which one is active.
*   `/model <name>`: Switch to a different profile (e.g., `/model reviewer`).
*   `/config`: Display current settings (context size, temperature, etc.).

## 🐚 Integrated Shell

Loom allows you to run shell commands without leaving the interface.

*   **Foreground Command (`!cmd`):** Runs the command and waits for it to finish. Output is streamed directly to you.
    *   Example: `!ls -la` or `!pytest tests/`
*   **Background Task (`!!cmd`):** Runs the command in the background, allowing you to continue talking to Loom while it works.
    *   Example: `!!npm run dev` or `!!docker-compose up`
    *   Use `/background` to see the status of all running background tasks.

## 🖱️ Web UI Features

The Web UI (launched via `loom --web`) adds several visual layers to the experience:

*   **Xterm.js Terminal:** A true ANSI-compatible terminal window that renders LLM output exactly like your favorite console.
*   **Stop Button:** Immediately kill the active agent task or a runaway shell command.
*   **Visual File Browser:** Use the folder icon in the header to navigate your server's filesystem and pick your project root.
*   **Session Browser:** Click the **Sessions** button to see a history of previous conversations and resume them with one click.
*   **URL States:** Bookmark or refresh your current session; the session ID is preserved in the URL.
