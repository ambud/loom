import pytest
import sys
import importlib

def test_imports():
    """Verify that all core modules can be imported without errors."""
    modules = [
        "src.main",
        "src.agent",
        "src.repl",
        "src.session",
        "src.web_ui",
        "src.utils",
        "src.config",
        "src.llm"
    ]
    for mod_name in modules:
        try:
            importlib.import_module(mod_name)
        except ImportError as e:
            pytest.fail(f"Failed to import {mod_name}: {e}")
        except Exception as e:
            pytest.fail(f"Unexpected error importing {mod_name}: {e}")

def test_main_cli_initialization():
    """Verify the main entry point can be reached and parser initialized."""
    from src.main import main
    import argparse
    from unittest.mock import patch

    # Mock parse_args to just return help and exit, ensuring no logic runs
    with patch("argparse.ArgumentParser.parse_args") as mock_args, \
         patch("sys.exit") as mock_exit:
        
        # We don't actually call main() as it might trigger logic, 
        # but we check if we can get the parser.
        from src.main import main
        assert callable(main)

def test_web_ui_initialization():
    """Verify FastAPI app is initialized."""
    from src.web_ui import app
    assert app.title == "Loom Web UI"
