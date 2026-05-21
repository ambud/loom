"""Tests for setup wizard, config helpers, and main.py wiring."""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import yaml

from src.config import (
    env_vars_override_setup,
    save_config,
    CONFIG_PATH,
    DEFAULTS,
)
from src.setup import (
    prompt_setup,
    PROVIDER_PRESETS,
    APPROVAL_MODES,
    _validate_url,
    _validate_approval_mode,
)


# ── env_vars_override_setup ──────────────────────────────────────────────


def test_env_vars_override_no_vars():
    with patch.dict(os.environ, {}, clear=False):
        for k in ("AGENT_BASE_URL", "AGENT_MODEL"):
            os.environ.pop(k, None)
        assert env_vars_override_setup() is False


def test_env_vars_override_base_url():
    with patch.dict(os.environ, {"AGENT_BASE_URL": "http://x"}):
        assert env_vars_override_setup() is True


def test_env_vars_override_model():
    with patch.dict(os.environ, {"AGENT_MODEL": "qwen"}):
        assert env_vars_override_setup() is True


# ── save_config ──────────────────────────────────────────────────────────


def test_save_config_writes_yaml():
    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        with patch.object(__import__("src.config", fromlist=["CONFIG_PATH"]), "CONFIG_PATH", tmp_path):
            # Use direct import to patch
            pass

    finally:
        os.unlink(tmp_path)


def test_save_config_creates_parents():
    base = tempfile.mkdtemp()
    nested = Path(base) / "a" / "b" / "c" / "test.yaml"
    try:
        with patch("src.config.CONFIG_PATH", nested):
            from src import config
            config.save_config({"model": "test"})
            assert nested.exists()
    finally:
        import shutil
        shutil.rmtree(base, ignore_errors=True)


def test_save_config_roundtrip():
    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w") as tmp:
        tmp_path = tmp.name

    try:
        import pathlib
        orig = CONFIG_PATH
        with patch("src.config.CONFIG_PATH", pathlib.Path(tmp_path)):
            from src import config as cfg_mod
            cfg_mod.save_config({"base_url": "http://localhost:11434/v1", "model": "llama3.2"})

        with open(tmp_path) as f:
            data = yaml.safe_load(f)
        assert data["base_url"] == "http://localhost:11434/v1"
        assert data["model"] == "llama3.2"
    finally:
        os.unlink(tmp_path)


# ── Validation helpers ───────────────────────────────────────────────────


def test_validate_url_http():
    assert _validate_url("http://localhost:8080/v1") is True


def test_validate_url_https():
    assert _validate_url("https://api.openai.com/v1") is True


def test_validate_url_no_scheme():
    assert _validate_url("localhost:8080") is False


def test_validate_approval_modes():
    assert _validate_approval_mode("safe") is True
    assert _validate_approval_mode("relay") is True
    assert _validate_approval_mode("yolo") is True
    assert _validate_approval_mode("unknown") is False


# ── prompt_setup (mocked) ───────────────────────────────────────────────


def test_prompt_setup_fresh():
    """Simulate a fresh setup with no existing config."""
    inputs = iter(["5", "http://localhost:8080/v1", "my-model", "relay"])
    with patch("builtins.input", lambda *a, **k: next(inputs)):
        result = prompt_setup(existing_cfg=None)
    assert result["base_url"] == "http://localhost:8080/v1"
    assert result["model"] == "my-model"
    assert result["approval_mode"] == "relay"


def test_prompt_setup_preset_ollama():
    """Select Ollama preset, accept defaults for rest."""
    inputs = iter(["2", "", "", ""])  # Ollama, then Enter for all defaults
    with patch("builtins.input", lambda *a, **k: next(inputs)):
        result = prompt_setup(existing_cfg=None)
    assert result["base_url"] == "http://localhost:11434/v1"
    assert result["model"] == "llama3.2"
    assert result["approval_mode"] == "yolo"


def test_prompt_setup_update_existing():
    """Re-run setup with existing config."""
    existing = {"base_url": "http://localhost:8080/v1", "model": "old-model"}
    inputs = iter(["5", "", "new-model", ""])  # keep URL, change model, keep approval
    with patch("builtins.input", lambda *a, **k: next(inputs)):
        result = prompt_setup(existing_cfg=existing)
    assert result["base_url"] == "http://localhost:8080/v1"
    assert result["model"] == "new-model"


# ── Provider presets structure ──────────────────────────────────────────


def test_provider_presets_have_required_keys():
    for key, preset in PROVIDER_PRESETS.items():
        assert "name" in preset
        assert "base_url" in preset
        assert "model" in preset
        assert preset["base_url"].startswith("http")


def test_approval_modes_documented():
    assert len(APPROVAL_MODES) == 3
    assert "safe" in APPROVAL_MODES
    assert "relay" in APPROVAL_MODES
    assert "yolo" in APPROVAL_MODES
