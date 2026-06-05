"""Pydantic şemaları — chat_sessions + chat_messages (UI sidebar için)."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ChatMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    role: str
    content: str
    created_at: datetime


class ChatSessionRead(BaseModel):
    """Sidebar listesi için özet."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str | None
    last_message_at: datetime
    created_at: datetime


class ChatSessionDetail(BaseModel):
    """Session açıldığında — mesajlar dahil."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str | None
    last_message_at: datetime
    created_at: datetime
    messages: list[ChatMessageRead] = Field(default_factory=list)
