"""Interactive first-run setup wizard for Loom."""

from __future__ import annotations

import sys
from typing import Optional

PROVIDER_PRESETS = {
    "1": {"name": "llama.cpp", "base_url": "http://localhost:8080/v1", "model": "llama"},
    "2": {"name": "Ollama", "base_url": "http://localhost:11434/v1", "model": "llama3.2"},
    "3": {"name": "LM Studio", "base_url": "http://localhost:1234/v1", "model": "llama"},
    "4": {"name": "OpenAI", "base_url": "https://api.openai.com/v1", "model": "gpt-4o"},
}

APPROVAL_MODES = {
    "safe": "Confirm every tool call",
    "relay": "Confirm writes and bash commands only",
    "yolo": "Auto-run all tools (default for local LLMs)",
}

_GREEN = "\033[32m"
_CYAN = "\033[36m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def _print_styled(text: str, style: str = "") -> None:
    codes = {
        "green": _GREEN,
        "cyan": _CYAN,
        "dim": _DIM,
        "bold": _BOLD,
    }
    wrapped = codes.get(style, "") + text + _RESET
    print(wrapped)


def _ask(
    label: str,
    default: str,
    current: Optional[str] = None,
    *,
    required: bool = True,
    validate: Optional[callable] = None,
) -> str:
    """Prompt for a value with default shown in brackets.

    Loops until the user provides a valid value or presses Enter (if not required).
    Returns the chosen value.
    """
    while True:
        parts = [label]
        if current:
            parts.append(f" {_DIM}(current: {current}){_RESET}")
        if default:
            parts.append(f" [")
            parts.append(f"{default}]")
        parts.append(": ")
        prompt_text = "".join(parts)

        try:
            answer = input(prompt_text).strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSetup cancelled.")
            sys.exit(130)

        if not answer:
            # Use current value if available, else default
            return current if current else default

        if validate and not validate(answer):
            print(f"  Invalid: {answer}")
            continue

        return answer


def _validate_url(url: str) -> bool:
    return url.startswith("http://") or url.startswith("https://")


def _validate_approval_mode(mode: str) -> bool:
    return mode in APPROVAL_MODES


def prompt_setup(existing_cfg: Optional[dict] = None) -> dict:
    """Run the interactive setup wizard.

    Args:
        existing_cfg: If provided, show current values and allow keeping/changing.

    Returns:
        Dict with base_url, model, approval_mode keys.
    """
    is_update = existing_cfg is not None and existing_cfg
    title = "Re-running Loom setup" if is_update else "Welcome to Loom"
    subtitle = "Let's configure your LLM connection."
    _print_styled(title, "bold green")
    _print_styled(subtitle, "dim")
    print()

    if is_update:
        _print_styled("Current configuration:", "cyan")
        for key in ("base_url", "model", "approval_mode"):
            val = existing_cfg.get(key, "(not set)")
            print(f"  {key}: {val}")
        print()

    # --- Provider preset selection ---
    print("Provider presets:")
    for key, preset in PROVIDER_PRESETS.items():
        print(f"  [{key}] {preset['name']:12s} {preset['base_url']}")
    print("  [5] Custom")
    print()

    preset_choice = _ask(
        "Select a preset",
        "5",
        existing_cfg.get("_preset") if is_update else None,
        required=False,
        validate=lambda x: x in ("1", "2", "3", "4", "5"),
    )

    # Determine defaults from preset or existing config
    if preset_choice in PROVIDER_PRESETS:
        preset = PROVIDER_PRESETS[preset_choice]
        url_default = preset["base_url"]
        model_default = preset["model"]
    else:
        url_default = "http://localhost:8080/v1"
        model_default = "llama"

    # Use existing values as defaults if updating
    if is_update:
        url_default = existing_cfg.get("base_url", url_default)
        model_default = existing_cfg.get("model", model_default)

    print()
    base_url = _ask(
        "LLM server base URL",
        url_default,
        existing_cfg.get("base_url") if is_update else None,
        validate=_validate_url,
    )
    model = _ask(
        "Model name",
        model_default,
        existing_cfg.get("model") if is_update else None,
    )

    # Approval mode
    approval_default = "yolo"
    if is_update:
        approval_default = existing_cfg.get("approval_mode", "yolo")

    mode_label = "Approval mode"
    mode_help = "(safe=confirm all, relay=confirm writes, yolo=auto-run)"
    approval_mode = _ask(
        f"{mode_label} {mode_help}",
        approval_default,
        existing_cfg.get("approval_mode") if is_update else None,
        validate=_validate_approval_mode,
    )

    return {
        "base_url": base_url,
        "model": model,
        "approval_mode": approval_mode,
    }
