from __future__ import annotations

from .events import to_sse


class StreamPublisher:
    @staticmethod
    def encode(event_name: str, payload: dict) -> str:
        return to_sse(event_name, payload)
