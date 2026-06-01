from __future__ import annotations

from pydantic import BaseModel
from .tools.contracts import ToolMetadata


class PolicyDecision(BaseModel):
    approval_required: bool = False
    allowed: bool = True
    retry_allowed: bool = True
    rate_limit: str = "default"
    execution_window: str = "always"
    reason: str = ""


class PolicyEngine:
    def evaluate(self, metadata: ToolMetadata, role: str) -> PolicyDecision:
        role_norm = (role or "").strip().lower()
        allowed = role_norm in [x.lower() for x in metadata.allowed_roles]
        decision = PolicyDecision(
            approval_required=bool(metadata.requires_approval),
            allowed=allowed,
            retry_allowed=bool(metadata.retry_allowed),
            rate_limit="default",
            execution_window="always",
            reason="" if allowed else "role_not_allowed",
        )
        return decision
