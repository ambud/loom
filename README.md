# 🧵 Loom

**Privacy First. Open Source First. Freedom First Access.**

Loom is a high-performance autonomous coding assistant built for developers who refuse to compromise on their digital sovereignty. It provides a truly open-source alternative to proprietary coding agents, running entirely on your hardware with **Zero Telemetry**.

![License](https://img.shields.io/badge/license-MIT-green)
![Python](https://img.shields.io/badge/python-3.10+-blue)
![Privacy](https://img.shields.io/badge/100%25-Privacy-brightgreen)
![Freedom](https://img.shields.io/badge/Freedom-First-orange)

<video src="https://github.com/user-attachments/assets/68cdbeaa-31e8-43c1-b1ef-19277ee19bbb" width="100%" controls autoplay loop muted></video>
---

## 🛡️ The Three Pillars of Loom

Loom is built on three fundamental principles that distinguish it from the "AI-as-a-Service" world:

### 1. Privacy First (Zero Telemetry)
Your code is your intellectual property. Loom contains **zero tracking**, zero "anonymous usage reporting," and zero background calls. It does not connect to any remote server unless you explicitly configure a remote LLM provider. What happens in Loom, stays in Loom.

### 2. Open Source First (Transparent & Auditable)
Loom is not just "source-available." It is a clean, minimal, and modular Python codebase designed to be audited in minutes. No hidden "backend" proprietary logic, no obfuscated weights—just transparent code that you can fork, modify, and improve.

### 3. Freedom First (Total Sovereignty)
No walled gardens. No forced updates. No account requirements. Loom gives you the freedom to run any model, on any hardware, for any length of time. You control the model, own the infrastructure, and own the data. This is AI access without the leash.

---

## ✨ Key Features

*   🤖 **Interface Agnostic:** Use the polished **Terminal REPL** or the modern **Web UI** (powered by Xterm.js).
*   🛡️ **Plan Mode:** Mandatory step-by-step planning with user approval before the agent touches your code.
*   🧠 **Cross-Session Memory:** Persistent, project-scoped learnings that allow Loom to get smarter as you work.
*   ⚡ **Surgical Edits:** Precise string replacements instead of full-file rewrites, saving tokens and preserving file integrity.
*   🔒 **Hardened Security:** JWT-based authentication, workspace root sandboxing, and configurable shell access.
*   🔄 **Resilience:** Automatic JSON repair for truncated tool calls and partial stream recovery from backend failures.
*   📈 **Token Metrics:** Detailed tracking of input and output tokens for every session and globally.
*   🗜️ **Smart Compaction:** Automatic context management using a "Head-Tail" summarization strategy. See [docs/COMPACTION.md](docs/COMPACTION.md).
*   🎯 **Task-Specific Models:** Dynamically switch models for different tasks. Use a high-logic model (like Deepseek V4 Pro) for the `/review` command while using a high-speed model for routine development—all running locally on your hardware.

---

## 🚀 Quick Start

### 1. Installation

```bash
# Clone the repo
git clone https://github.com/ambud/loom.git
cd loom

# Install in editable mode
pip install -e .
```

### 2. Launch Llama.cpp Server

Loom works best with `llama-server`. We recommend the **Qwen 3.6 27B** model.

```bash
# Recommended high-performance command for Qwen 3.6 27B
llama.cpp/build/bin/llama-server -m Qwen3.6-27B-UD-Q8_K_XL.gguf \
    -ngl 99 -t 12 --ctx-size 262000 \
    --temp 1.0 --top-p 0.95 --min-p 0.05 \
    -ctk q8_0 -ctv q8_0 --flash-attn on \
    --host 0.0.0.0 --port 8080 --alias Qwen36-27 -sm layer
```

### 3. Start Loom

**Terminal Mode:**
```bash
loom
```

**Web UI Mode:**
```bash
loom --web
```
*Access via http://0.0.0.0:8000. Default user: `admin`.*

---

## 🛠️ Deep Dive: Llama.cpp Integration

Loom is optimized for the `llama.cpp` ecosystem. To get the best experience, use these recommended settings for your server:

### Recommended `llama-server` Flags
| Flag | Recommendation | Why? |
|---|---|---|
| `--ctx-size` | `262000` | Large context allows for full-codebase analysis. |
| `--flash-attn` | `on` | Dramatically improves performance on supported hardware. |
| `--alias` | `Qwen36-27` | Provides a clean model name for Loom's configuration. |

### Model Recommendations
1.  **Qwen 3.6 27B:** Currently the gold standard for local coding. High instruction-following accuracy for JSON tool calls.
2.  **Qwen 3.6 35B-A3B:** The high-parameter variant for advanced logic and large-scale refactoring.
3.  **Deepseek V4 Flash:** Optimized for speed and low-latency agentic workflows.

---

## 💻 Interfaces

### Modern Web UI
*   **Terminal-like feel:** Powered by Xterm.js for full ANSI color support.
*   **Visual File Browser:** Navigate your server's filesystem and select projects visually.
*   **Session Management:** Browse, rename, and resume previous conversations with full history.
*   **Security:** Forced password change on first login, securely hashed via Argon2.

### Power-User CLI
*   **Slash Commands:** Quick access to system tools. For a full list, see the [Usage Guide](docs/USAGE_GUIDE.md).
*   **Direct Shell access:** Run `!ls` for foreground commands or `!!npm run dev` for background processes.

### Model Profiles & Review
Loom supports **Named Profiles** to let you quickly switch between different LLM configurations.

*   **Profiles:** Defined under `models:` in your config. Each profile can override the `model`, `base_url`, and `temperature`.
*   **`review_profile`:** Specifies which profile Loom should automatically switch to when you run the `/review` command. This allows you to use a heavy-duty model (like Deepseek V4 Pro) for code analysis while keeping a faster model active for daily tasks.
*   **Switching:** Use the `/model <name>` command in the REPL or Web UI to swap profiles on the fly.

### Customizing Prompts
Loom's core intelligence is driven by Markdown prompts. You can find the defaults in `system.md` and `review_system.md`. For instructions on how to provide your own overrides, see [docs/CUSTOM_PROMPTS.md](docs/CUSTOM_PROMPTS.md).

---

## 🛡️ Security & Privacy

Loom is built for tinkerers and homelab enthusiasts. For a detailed breakdown of our authentication and sandboxing model, see [docs/SECURITY.md](docs/SECURITY.md).
*   **Workspace Sandboxing:** Restrict Loom's access to specific directories via `workspace_root`.
*   **RCE Governance:** Disable shell command execution entirely for restricted environments.
*   **Automatic Secret Keys:** Unique JWT signing keys are auto-generated on first launch.
*   **Zero Telemetry:** No data ever leaves your machine unless you explicitly configure a remote LLM provider.

---

## ⚙️ Configuration

Loom stores everything in `~/.loom/`. You can customize behavior in `~/.loom/config.yaml`:

```yaml
# Model URL mapping for easy switching
model_mapping:
  "gpt-4o": "https://api.openai.com/v1"
  "Qwen36-27": "http://localhost:8080/v1"

# Security settings
workspace_root: "~/projects/my-app"
allow_shell_commands: true

# Defaults
model: "Qwen36-27"
temperature: 0.0
max_tokens: 0 # Resolves to 250,000 (unlimited)
```

---

## 🤝 Contributing

We love tinkerers! Feel free to open issues, suggest tools, or submit PRs.

1.  Fork the repository.
2.  Create your feature branch (`git checkout -b feature/amazing-tool`).
3.  Commit your changes (`git commit -m 'feat: add support for tool X'`).
4.  Push to the branch (`git push origin feature/amazing-tool`).
5.  Open a Pull Request.

---

## 📜 License

Distributed under the MIT License. See `LICENSE` for more information.

---
*Built by tinkerer, for tinkerers. Copyright (c) 2026 Ambud Sharma.*
