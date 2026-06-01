from __future__ import annotations

from typing import Awaitable, Callable
from .tools.contracts import ToolContext, ToolMetadata, ToolResult


ToolHandler = Callable[[ToolContext], Awaitable[ToolResult]]


class ToolRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, tuple[ToolMetadata, ToolHandler]] = {}

    def register(self, metadata: ToolMetadata, handler: ToolHandler) -> None:
        self._handlers[metadata.tool] = (metadata, handler)

    def get(self, tool: str) -> tuple[ToolMetadata, ToolHandler] | None:
        return self._handlers.get(tool)

    def snapshot(self) -> dict[str, dict]:
        return {
            key: row[0].model_dump(mode="python")
            for key, row in self._handlers.items()
        }
