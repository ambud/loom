"""Base class for tool definitions and a registry."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any


class Tool(ABC):
    """Abstract base for all tools."""

    name: str
    description: str

    @abstractmethod
    async def run(self, **kwargs) -> str:
        """Execute the tool and return a string result for the LLM."""


class ToolRegistry:
    """Maps tool name -> Tool instance and collects JSON schemas."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    async def dispatch(self, name: str, **kwargs) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return f"Error: unknown tool '{name}'"
        try:
            return await tool.run(**kwargs)
        except Exception as exc:
            return f"Error running tool '{name}': {exc}"

    @property
    def tools(self) -> dict[str, Tool]:
        return self._tools
