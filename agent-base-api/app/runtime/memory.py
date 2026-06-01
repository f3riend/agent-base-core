from __future__ import annotations


class MemoryStore:
    """Abstraction for short/long/entity/workspace memory layers."""

    def __init__(self) -> None:
        self.short_term_memory: dict[str, list[dict]] = {}
        self.long_term_memory: dict[str, list[dict]] = {}
        self.entity_memory: dict[str, list[dict]] = {}
        self.workspace_memory: dict[str, list[dict]] = {}

    def append_short_term(self, workspace_id: str, conversation_id: str, item: dict) -> None:
        key = f"{workspace_id}:{conversation_id}"
        self.short_term_memory.setdefault(key, []).append(dict(item))
        self.short_term_memory[key] = self.short_term_memory[key][-100:]

    def append_entity_memory(self, workspace_id: str, entity_type: str, entity_id: str, item: dict) -> None:
        key = f"{workspace_id}:{entity_type}:{entity_id}"
        self.entity_memory.setdefault(key, []).append(dict(item))
        self.entity_memory[key] = self.entity_memory[key][-200:]

    def get_entity_memory(self, workspace_id: str, entity_type: str, entity_id: str) -> list[dict]:
        key = f"{workspace_id}:{entity_type}:{entity_id}"
        return list(self.entity_memory.get(key) or [])

    def semantic_search(self, workspace_id: str, query: str, limit: int = 5) -> list[dict]:
        """Vector layer abstraction point (pgvector/qdrant)."""
        _ = workspace_id, query, limit
        return []
