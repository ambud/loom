"""Configuration loader."""

from __future__ import annotations

import copy
import os
import secrets
import sys
from pathlib import Path

import yaml
from openai import AsyncOpenAI

DEFAULTS = {
    "model": "Qwen36-27",
    "base_url": "http://localhost:8080/v1",
    "max_tokens": 0,
    "temperature": 0.0,
    "system_prompt_file": str(Path(__file__).resolve().parent.parent / "system.md"),
    "max_tool_rounds": 25,
    "approval_mode": "yolo",
    "plan": False,
    "stream_text": True,
    "session_log_dir": str(Path.home() / ".loom" / "sessions"),
    "context_window": 262000,
    "compaction_threshold": 0.80,  # auto-compact when context reaches 80%
    "compaction_keep_last_turns": 3,  # keep last 3 user turns after compaction
    "models": {},
    "model_mapping": {
        "Qwen36-27": "http://localhost:8080/v1",
    },
    "review_profile": "reviewer",
    "web_user": "admin",
    "web_password": "",  # Empty means no auth unless set
    "secret_key": "loom-default-secret-change-me",
    "workspace_root": ".",
    "allow_shell_commands": True,
}

CONFIG_PATH = Path(os.environ.get("AGENT_CONFIG", str(Path.home() / ".loom" / "config.yaml")))


def _resolve_max_tokens(raw_value) -> int:
    raw = str(raw_value)
    if raw in ("-1", "infinite", "0"):
        return 250000
    try:
        return int(raw)
    except (ValueError, TypeError):
        return 250000


def load() -> dict:
    """Load config from file, merged with env overrides."""
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            cfg.update(yaml.safe_load(f) or {})

    # Security: Generate a random secret key on first run if it's the default
    if cfg.get("secret_key") == DEFAULTS["secret_key"]:
        cfg["secret_key"] = secrets.token_hex(32)
        if CONFIG_PATH.exists():
            save_config(cfg)
    # env var overrides
    cfg["base_url"] = os.environ.get("AGENT_BASE_URL", cfg["base_url"])
    cfg["model"] = os.environ.get("AGENT_MODEL", cfg["model"])
    cfg["max_tokens"] = _resolve_max_tokens(
        os.environ.get("AGENT_MAX_TOKENS", str(cfg["max_tokens"]))
    )
    cfg["temperature"] = float(
        os.environ.get("AGENT_TEMPERATURE", str(cfg["temperature"]))
    )
    cfg["approval_mode"] = os.environ.get("AGENT_APPROVAL_MODE", cfg["approval_mode"])
    no_stream = os.environ.get("AGENT_NO_STREAM", "")
    if no_stream.lower() in ("1", "true", "yes"):
        cfg["stream_text"] = False
    
    # context window overrides
    if os.environ.get("AGENT_CONTEXT_WINDOW"):
        cfg["context_window"] = int(os.environ["AGENT_CONTEXT_WINDOW"])
    if os.environ.get("AGENT_COMPACTION_THRESHOLD"):
        cfg["compaction_threshold"] = float(os.environ["AGENT_COMPACTION_THRESHOLD"])
    if os.environ.get("AGENT_COMPACTION_KEEP_LAST_TURNS"):
        cfg["compaction_keep_last_turns"] = int(os.environ["AGENT_COMPACTION_KEEP_LAST_TURNS"])

    # Resolve base_url via model_mapping if it's still the default and model changed
    mapping = cfg.get("model_mapping", {})
    if (cfg["base_url"] == DEFAULTS["base_url"] and 
        cfg["model"] in mapping and 
        cfg["model"] != DEFAULTS["model"]):
        cfg["base_url"] = mapping[cfg["model"]]

    # Ensure models dict exists
    if "models" not in cfg or not isinstance(cfg["models"], dict):
        cfg["models"] = {}
    # AGENT_REVIEW_MODEL / AGENT_REVIEW_BASE_URL auto-create/override the reviewer profile
    review_model = os.environ.get("AGENT_REVIEW_MODEL")
    review_base_url = os.environ.get("AGENT_REVIEW_BASE_URL")
    if review_model or review_base_url:
        reviewer = cfg["models"].setdefault("reviewer", {})
        if review_model:
            reviewer["model"] = review_model
        if review_base_url:
            reviewer["base_url"] = review_base_url
    return cfg


def env_vars_override_setup() -> bool:
    """Return True if key env vars are set, meaning setup is unnecessary."""
    return bool(os.environ.get("AGENT_BASE_URL") or os.environ.get("AGENT_MODEL"))


def save_config(user_cfg: dict) -> Path:
    """Write cfg dict to CONFIG_PATH as YAML.

    Creates parent directories as needed.
    Returns the path written to.
    """
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        yaml.safe_dump(user_cfg, f, default_flow_style=False)
    return CONFIG_PATH


def get_model_cfg(cfg: dict, name: str) -> dict:
    """Merge top-level config with a named model profile's overrides."""
    merged = copy.deepcopy(cfg)
    models = cfg.get("models", {})
    profile = models.get(name)
    if profile:
        merged.update(profile)
        # If profile changed model but not base_url, check mapping
        if "model" in profile and "base_url" not in profile:
            mapping = merged.get("model_mapping", {})
            if profile["model"] in mapping:
                merged["base_url"] = mapping[profile["model"]]
    # "default" profile mirrors top-level settings
    if name == "default" and "default" in models:
        merged.update(models["default"])
    return merged



def make_client(model_cfg: dict) -> AsyncOpenAI:
    """Create an AsyncOpenAI client from a model config dict."""
    # Prioritize environment variables for keys, fallback to local default
    api_key = (
        os.environ.get("AGENT_API_KEY") or 
        os.environ.get("OPENAI_API_KEY") or 
        "sk-llama-cpp-local"
    )
    return AsyncOpenAI(
        base_url=model_cfg.get("base_url", "http://localhost:8080/v1"),
        api_key=api_key,
    )


def load_system_prompt(cfg: dict) -> str:
    """Load the system prompt markdown file, with memory index appended."""
    path = Path(cfg.get("system_prompt_file", DEFAULTS["system_prompt_file"]))
    if not path.exists():
        path = Path(DEFAULTS["system_prompt_file"])
    prompt = ""
    if path.exists():
        prompt = path.read_text()
    else:
        prompt = "You are a helpful coding assistant."

    # Inject memory index
    from .tools.memory import load_memory_index
    mem = load_memory_index()
    if mem:
        prompt += "\n\n# Cross-Session Memory\n\n"
        prompt += "Stored learnings from previous sessions:\n\n"
        prompt += mem + "\n"
        prompt += "Use the `memory` tool to read full topic files, search, or store new memories.\n"

    return prompt


def load_review_system_prompt(cfg: dict) -> str:
    """Load the review system prompt markdown file."""
    path = Path(cfg.get("review_system_prompt_file", str(Path(__file__).resolve().parent.parent / "review_system.md")))
    if not path.exists():
        path = Path(DEFAULTS["system_prompt_file"]).parent / "review_system.md"
    if path.exists():
        return path.read_text()
    return "You are a code review specialist. Analyze changes for bugs, security issues, performance problems, and convention violations. Do not modify code."
