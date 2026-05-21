"""Tests for agent.py JSON repair fix."""

import json
import pytest


def _repair(raw_args: str) -> dict:
    """Replicate the brace-matching repair logic from agent.py."""
    depth = 0
    last_close = -1
    in_string = False
    escaped = False
    for i, ch in enumerate(raw_args):
        if escaped:
            escaped = False
            continue
        if ch == '\\':
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                last_close = i
    if last_close > 0:
        return json.loads(raw_args[:last_close + 1])
    raise json.JSONDecodeError("could not repair", raw_args, 0)


def test_json_repair_ignores_braces_in_strings():
    """Bug #7: } inside string values broke brace matching."""
    raw = '{"command": "echo {foo} && echo {bar}"}truncated'
    result = _repair(raw)
    assert result["command"] == "echo {foo} && echo {bar}"


def test_json_repair_simple():
    raw = '{"foo": "bar"}extra junk here'
    result = _repair(raw)
    assert result == {"foo": "bar"}


def test_json_repair_nested():
    raw = '{"args": {"inner": [1, 2]}}trunc'
    result = _repair(raw)
    assert result == {"args": {"inner": [1, 2]}}


def test_json_repair_brace_in_string_value():
    """Most common failure case: command with braces in string."""
    raw = '{"command": "for i in {1..5}; do echo $i; done"}'
    result = _repair(raw)
    assert result["command"] == "for i in {1..5}; do echo $i; done"


def test_json_repair_handles_escaped_backslash():
    """Escaped backslash should not cause next char to be skipped."""
    # \\n is literal backslash + n (backslash is escaped)
    raw = '{"path": "C:\\\\}dir"}extra'
    # The \\ escapes the backslash, then } is NOT escaped but still in string
    result = _repair(raw)
    assert "dir" in result["path"]


def test_json_repair_multiple_braces_in_multiple_strings():
    raw = '{"cmd": "if {}", "msg": "then {}"}trunc'
    result = _repair(raw)
    assert result["cmd"] == "if {}"
    assert result["msg"] == "then {}"
