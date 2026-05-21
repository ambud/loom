#!/usr/bin/env python3
"""Convenience entrypoint — run from project root without installing.

Usage:
    python run.py "explain the codebase structure"
    python run.py -i              # interactive mode
    python run.py -m qwen -u http://localhost:8080/v1 "your task"
"""
import sys
import os

# Ensure the project root is on sys.path so `src` is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.main import main

if __name__ == "__main__":
    main()
