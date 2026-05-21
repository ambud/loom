import os
from pathlib import Path
from src.config import load, get_model_cfg, DEFAULTS

def test_model_mapping_resolution():
    # Mock environment variables
    os.environ["AGENT_MODEL"] = "gpt4"
    # Ensure AGENT_BASE_URL is not set to allow mapping to take effect
    if "AGENT_BASE_URL" in os.environ:
        del os.environ["AGENT_BASE_URL"]
    
    # We need to inject the mapping since it's not in DEFAULTS for gpt4
    # Normally this would come from the config file
    cfg = load()
    cfg["model_mapping"]["gpt4"] = "https://api.openai.com/v1"
    
    # Re-trigger resolution (since load() already ran once in the test setup)
    # Actually, let's mock DEFAULTS for a cleaner test if possible, 
    # but load() uses DEFAULTS directly.
    
    # Let's test get_model_cfg directly which is easier to isolate
    base_cfg = {
        "model": "llama",
        "base_url": "http://localhost:8080/v1",
        "model_mapping": {
            "llama": "http://localhost:8080/v1",
            "gpt4": "https://api.openai.com/v1"
        },
        "models": {
            "openai_profile": {
                "model": "gpt4"
            }
        }
    }
    
    # 1. Test profile override with mapping
    merged = get_model_cfg(base_cfg, "openai_profile")
    assert merged["model"] == "gpt4"
    assert merged["base_url"] == "https://api.openai.com/v1"
    
    # 2. Test profile override WITHOUT mapping (should keep top-level base_url)
    base_cfg["models"]["unknown_profile"] = {"model": "unknown"}
    merged = get_model_cfg(base_cfg, "unknown_profile")
    assert merged["model"] == "unknown"
    assert merged["base_url"] == "http://localhost:8080/v1"

    # 3. Test profile WITH explicit base_url (should NOT use mapping)
    base_cfg["models"]["explicit_profile"] = {
        "model": "gpt4",
        "base_url": "http://explicit.url"
    }
    merged = get_model_cfg(base_cfg, "explicit_profile")
    assert merged["model"] == "gpt4"
    assert merged["base_url"] == "http://explicit.url"

def test_load_with_env_mapping(monkeypatch):
    # Use monkeypatch to isolate env changes
    monkeypatch.setenv("AGENT_MODEL", "mapped_model")
    monkeypatch.delenv("AGENT_BASE_URL", raising=False)
    
    # Mock DEFAULTS or CONFIG_PATH is hard, but we can check if it uses mapping
    # if we could inject into DEFAULTS.
    import src.config
    original_defaults = src.config.DEFAULTS.copy()
    try:
        src.config.DEFAULTS["model_mapping"]["mapped_model"] = "http://mapped.url"
        cfg = load()
        assert cfg["model"] == "mapped_model"
        assert cfg["base_url"] == "http://mapped.url"
    finally:
        src.config.DEFAULTS = original_defaults
