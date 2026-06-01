from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field


class ToolMetadata(BaseModel):
    tool: str
    type: str
    provider: str
    risk_level: Literal["low", "medium", "high"] = "low"
    requires_approval: bool = False
    allowed_roles: list[str] = Field(default_factory=lambda: ["admin"])
    retry_allowed: bool = True


class ToolContext(BaseModel):
    workspace_id: str
    operation_id: str
    conversation_id: str
    user_id: str
    user_role: str = "operator"
    message: str
    intent: str
    entity_type: str = "product"
    entity_id: str = ""
    context: dict = Field(default_factory=dict)
    metadata: dict = Field(default_factory=dict)


class ToolResult(BaseModel):
    status: Literal["completed", "pending", "failed"] = "completed"
    output: dict = Field(default_factory=dict)
    preview: str | None = None
    image_url: str | None = None
    metadata: dict = Field(default_factory=dict)
