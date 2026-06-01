from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pydantic import BaseModel, Field


EVENT_VERSION = 1


class EventEnvelope(BaseModel):
    event_id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:16]}")
    event_version: int = EVENT_VERSION
    event_type: str
    workspace_id: str
    operation_id: str
    entity_type: str = "product"
    entity_id: str = ""
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    correlation_id: str = ""
    causation_id: str = ""
    payload: dict = Field(default_factory=dict)


def to_sse(event_name: str, payload: dict) -> str:
    return f"event: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
