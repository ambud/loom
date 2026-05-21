"""Memory tool: cross-session persistent knowledge store.

Stores topic-based markdown files under ~/.loom/memory/<project>/ where
<project> is derived from the current working directory path.
The MEMORY.md index is auto-injected into the system prompt each session.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from .base import Tool

MEMORY_BASE = Path.home() / ".loom" / "memory"
MAX_INDEX_LINES = 200


def _project_dir() -> Path:
    """Return a per-project memory directory scoped to the current working directory."""
    cwd_hash = hashlib.sha256(os.getcwd().encode()).hexdigest()[:12]
    proj = MEMORY_BASE / cwd_hash
    proj.mkdir(parents=True, exist_ok=True)
    return proj


MEMORY_DIR = _project_dir()
MEMORY_INDEX = MEMORY_DIR / "MEMORY.md"


def _ensure_dir():
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def _find_or_create_topic(text: str) -> str:
    """Suggest a topic file name based on content keywords."""
    text_lower = text.lower()
    topic_hints = {
        "convention": "conventions.md",
        "preference": "preferences.md",
        "workflow": "preferences.md",
        "bug": "bugs.md",
        "debug": "bugs.md",
        "project": "projects.md",
        "codebase": "projects.md",
        "security": "security.md",
        "auth": "security.md",
        "api": "api.md",
        "database": "data.md",
        "db": "data.md",
        "test": "testing.md",
        "deploy": "deployment.md",
        "ci/cd": "deployment.md",
    }
    for keyword, topic in topic_hints.items():
        if keyword in text_lower:
            return topic
    return "notes.md"


class MemoryTool(Tool):
    name = "memory"
    description = (
        "Store or search persistent cross-session memories. "
        "Action 'store': save a learning/fact. Action 'search': find memories by keyword. "
        "Action 'list': show all topics. Action 'read': read a specific topic file."
    )

    async def run(
        self,
        action: str = "list",
        topic: str | None = None,
        text: str | None = None,
        keyword: str | None = None,
        **_,
    ) -> str:
        _ensure_dir()

        if action == "store":
            return self._store(text or "")
        if action == "search":
            return self._search(keyword or "")
        if action == "list":
            return self._list()
        if action == "read":
            return self._read(topic or "MEMORY.md")
        return f"Unknown memory action: {action}. Use: store, search, list, read."

    def _store(self, text: str) -> str:
        """Store a memory entry under a topic file."""
        topic = _find_or_create_topic(text)
        topic_path = MEMORY_DIR / topic

        entry = f"- {text}"
        exists = topic_path.exists() and topic_path.read_text().strip()
        if exists and text in exists:
            return f"Memory already exists in {topic}."

        if exists and not exists.endswith("\n"):
            exists += "\n"
        with open(topic_path, "a") as f:
            f.write(entry + "\n")

        # Update index
        self._update_index(topic)
        return f"Stored in {topic}"

    def _search(self, keyword: str) -> str:
        """Search all memory files for a keyword."""
        results = []
        pattern = re.compile(re.escape(keyword), re.IGNORECASE)
        for fp in sorted(MEMORY_DIR.iterdir()):
            if fp.name == "MEMORY.md" or not fp.suffix:
                continue
            try:
                content = fp.read_text()
            except OSError:
                continue
            for i, line in enumerate(content.splitlines(), 1):
                if pattern.search(line):
                    results.append(f"  {fp.name}:{i} {line}")

        if not results:
            return f"No memories matching '{keyword}'."
        return "Memory search results:\n" + "\n".join(results)

    def _list(self) -> str:
        """List all memory topic files."""
        topics = []
        for fp in sorted(MEMORY_DIR.iterdir()):
            if fp.name == "MEMORY.md":
                continue
            if fp.suffix in (".md", ".txt"):
                lines = fp.read_text().strip().count("\n") + 1
                topics.append(f"  {fp.name} ({lines} entries)")

        if not topics:
            return "No memories stored yet. Use action='store', text='...' to save."
        return "Memory topics:\n" + "\n".join(topics)

    def _read(self, topic: str) -> str:
        """Read a specific topic file."""
        if not topic.endswith(".md"):
            topic += ".md"
        fp = MEMORY_DIR / topic
        if not fp.exists():
            return f"Memory file not found: {topic}"
        return f"--- {topic} ---\n" + fp.read_text()

    def _update_index(self, topic: str) -> None:
        """Append to or update MEMORY.md index."""
        _ensure_dir()
        existing = MEMORY_INDEX.read_text() if MEMORY_INDEX.exists() else ""

        # Check if topic already indexed
        if topic in existing:
            return

        with open(MEMORY_INDEX, "a") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            f.write(f"- {topic}\n")


def load_memory_index() -> str:
    """Load the MEMORY.md index for injection into system prompt."""
    if not MEMORY_INDEX.exists():
        return ""
    lines = MEMORY_INDEX.read_text().splitlines()
    # Also load each topic file's entries for richer context
    result = []
    for line in lines:
        line = line.strip()
        if line.startswith("- "):
            topic = line[2:].strip()
            topic_path = MEMORY_DIR / topic
            if topic_path.exists():
                entries = topic_path.read_text().strip().splitlines()
                entry_text = entries[0] if entries else ""
                if entry_text.startswith("- "):
                    entry_text = entry_text[2:]
                result.append(f"- **[{topic}]** {entry_text}")
            else:
                result.append(f"- **[{topic}]**")

    text = "\n".join(result)
    if len(text.splitlines()) > MAX_INDEX_LINES:
        truncated = text.splitlines()[:MAX_INDEX_LINES]
        truncated.append(f"\n... ({len(text.splitlines()) - MAX_INDEX_LINES} more entries truncated)")
        text = "\n".join(truncated)
    return text
