# Security Model

Loom is designed with a "Tinkerer-First" security model. It balances the need for absolute local power (like shell access) with robust protections for remote access.

## 1. Authentication

Loom implements a tiered authentication system for the Web UI:

### Password Hashing
*   **Mechanism:** Argon2 (via `passlib`). 
*   **First Login:** Loom forces a password change on the first login if a default password is detected. 
*   **Storage:** Passwords are never stored in plain text after the first change. They are hashed and persisted to `~/.loom/config.yaml`.

### JWT (JSON Web Tokens)
*   All Web UI interactions (REST and WebSocket) require a valid JWT.
*   **Secret Key:** A unique, high-entropy secret key is auto-generated on the first launch and stored locally. This ensures that even if two users have identical configurations, their tokens are not interchangeable.
*   **Expiration:** Tokens are issued with a 7-day expiration by default.

## 2. Authorization & Sandboxing

### Workspace Root
*   The `workspace_root` configuration setting defines the boundary for Loom's file operations.
*   **Enforcement:** The file browser, `read_file`, `write_file`, and `edit_file` tools strictly validate that every target path is within the normalized workspace root.
*   **Traversal Protection:** Any attempt to use `..` to escape the root is caught by our path validation logic and blocked.

### Shell Access (RCE)
*   **The Risk:** Loom provides full shell access via the `!shell` command and the `bash` tool. In a web-exposed environment, this is equivalent to Remote Code Execution (RCE).
*   **Control:** The `allow_shell_commands` toggle in `config.yaml` allows you to disable this feature entirely.
*   **Recommendation:** Only enable shell commands if your Loom instance is behind a trusted VPN or restricted to your local network.

## 3. Data Privacy

*   **Zero Telemetry:** Loom has no "phone home" functionality. We do not collect usage stats, crash reports, or code snippets.
*   **Local-Only by Default:** All processing happens on your specified `base_url`. No data leaves your network unless you explicitly configure a remote LLM provider (like OpenAI).
*   **Auditability:** The core logic is contained in a few small Python files. We encourage all users to audit the code themselves.
