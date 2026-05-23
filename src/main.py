#!/usr/bin/env python3
"""llama-agent CLI — a coding agent powered by llama.cpp server."""

from __future__ import annotations

import argparse
import asyncio
import sys

from openai import AsyncOpenAI

from .config import load, get_model_cfg, make_client, load_system_prompt
from .config import CONFIG_PATH, save_config
from .llm import TOOLS
from .agent import run_turns
from .tools import create_registry


def _run_setup(existing_cfg: dict | None) -> None:
    """Run the interactive setup wizard and save config."""
    from .setup import prompt_setup

    if not hasattr(sys.stdin, "isatty") or not sys.stdin.isatty():
        print(
            "Error: --setup requires an interactive terminal. "
            "Configure via env vars or create ~/.loom.yaml manually.",
            file=sys.stderr,
        )
        sys.exit(1)

    answers = prompt_setup(existing_cfg=existing_cfg)
    try:
        save_config(answers)
    except OSError as exc:
        print(f"Error writing config: {exc}", file=sys.stderr)
        sys.exit(1)

    verb = "Updated" if existing_cfg else "Written"
    print(f"Config {verb} to {CONFIG_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="loom",
        description="Privacy first coding agent CLI powered by llama.cpp",
    )
    parser.add_argument("prompt", nargs="?", help="The task description / prompt for the agent")
    parser.add_argument("-m", "--model", help="Model name")
    parser.add_argument("-u", "--base-url", help="llama.cpp API base URL")
    parser.add_argument("-s", "--system-prompt", help="Path to custom system prompt .md file")
    parser.add_argument("-t", "--temperature", type=float, help="Sampling temperature")
    parser.add_argument("--max-tokens", type=int, help="Max tokens per completion")
    parser.add_argument(
        "--approval-mode",
        choices=["safe", "relay", "yolo"],
        help="Tool approval: safe=confirm all, relay=confirm writes/bash, yolo=none",
    )
    parser.add_argument("--plan", action="store_true", help="Plan mode: agent must propose a plan before executing tools")
    parser.add_argument("--no-stream", action="store_true", help="Disable streaming text output")
    parser.add_argument("--profile", help="Named model profile from config (e.g. reviewer)")
    parser.add_argument("--review-model", help="Model to use for /review (overrides config/env)")
    parser.add_argument("--review-base-url", help="Base URL for /review model (overrides config/env)")
    parser.add_argument("--setup", action="store_true", help="Run interactive setup wizard")
    parser.add_argument("--skip-setup", action="store_true", help="Skip first-run setup")
    parser.add_argument("--resume", help="Resume a previous session by ID")
    parser.add_argument("--web", action="store_true", help="Launch the Web UI server")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind the Web UI server to (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind the Web UI server to (default: 8000)")

    args = parser.parse_args()

    if args.web:
        from .web_ui import start_web_ui
        start_web_ui(host=args.host, port=args.port)
        return

    # --- Setup gate ---

    if args.setup and args.skip_setup:
        print("Note: --skip-setup ignored because --setup was also passed.", file=sys.stderr)

    if args.setup:
        # Always run setup, merge with existing config
        import yaml
        existing = {}
        if CONFIG_PATH.exists():
            try:
                existing = yaml.safe_load(CONFIG_PATH.read_text()) or {}
            except Exception:
                existing = {}
        _run_setup(existing_cfg=existing if existing else None)
    
    cfg = load()

    # Apply named profile first, then CLI overrides on top
    if args.profile:
        cfg = get_model_cfg(cfg, args.profile)
    if args.model:
        cfg["model"] = args.model
    if args.base_url:
        cfg["base_url"] = args.base_url
    if args.temperature is not None:
        cfg["temperature"] = args.temperature
    if args.max_tokens in (-1, 0):
        cfg["max_tokens"] = 250000
    elif args.max_tokens:
        cfg["max_tokens"] = args.max_tokens
    if args.system_prompt:
        cfg["system_prompt_file"] = args.system_prompt
    if args.approval_mode:
        cfg["approval_mode"] = args.approval_mode
    if args.plan:
        cfg["plan"] = True
    if args.no_stream:
        cfg["stream_text"] = False
    # CLI overrides for reviewer profile
    if args.review_model or args.review_base_url:
        reviewer = cfg.setdefault("models", {}).setdefault("reviewer", {})
        if args.review_model:
            reviewer["model"] = args.review_model
        if args.review_base_url:
            reviewer["base_url"] = args.review_base_url

    client = make_client(cfg)
    system_prompt = load_system_prompt(cfg)

    if args.prompt:
        # One-shot mode
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": args.prompt},
        ]
        from .llm import close_http_client
        try:
            asyncio.run(run_turns(client, messages, TOOLS, create_registry(), cfg))
        except KeyboardInterrupt:
            print("\nInterrupted.")
            sys.exit(130)
        finally:
            # We need a new loop or use the existing one to close the client
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(close_http_client())
                else:
                    loop.run_until_complete(close_http_client())
            except Exception:
                pass
    else:
        # Interactive REPL with readline history, slash commands, session logging
        from .repl import interactive_loop

        try:
            asyncio.run(interactive_loop(
                client, system_prompt, cfg, TOOLS, create_registry(),
                active_profile=args.profile or "default",
                session_id=args.resume,
            ))
        except KeyboardInterrupt:
            print("\nGoodbye.")
            sys.exit(130)


if __name__ == "__main__":
    main()
