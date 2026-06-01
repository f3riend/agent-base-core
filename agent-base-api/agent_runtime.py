"""
Multi-agent coordination preparation — routing abstraction, shared context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent_registry import AGENT_REGISTRY, get_agent_for_route


@dataclass
class AgentContext:
    user_id: int
    route: str
    business_state: dict = field(default_factory=dict)
    bi_insights: dict = field(default_factory=dict)
    cross_event: dict = field(default_factory=dict)
    memory_summary: str = ""
    compressed_timeline: str = ""


@dataclass
class AgentTask:
    agent_id: str
    task_type: str
    payload: dict
    priority: int = 50


class AgentRuntime:
    """Future multi-agent orchestration facade."""

    def __init__(self, user_id: int = 1):
        self.user_id = user_id
        self.agents = {a.id: a for a in AGENT_REGISTRY}

    def build_shared_context(
        self,
        route: str,
        business_state: dict | None = None,
        bi: dict | None = None,
        cross: dict | None = None,
    ) -> AgentContext:
        from business_state import build_business_state
        from context_compressor import build_business_summary

        state = business_state or build_business_state(self.user_id)
        bi_data = bi or {}
        cross_data = cross or {}
        summary = build_business_summary(
            state,
            bi_data.get("insights", []),
            cross_data,
        )
        return AgentContext(
            user_id=self.user_id,
            route=route,
            business_state=state,
            bi_insights=bi_data,
            cross_event=cross_data,
            memory_summary=summary,
        )

    def route_to_agent(self, route: str, business_intent: str) -> str:
        spec = get_agent_for_route(route)
        if business_intent in ("inventory_risk", "low_stock"):
            return "inventory"
        return spec.id if spec else "marketing"

    def delegate(self, ctx: AgentContext, task: AgentTask) -> dict:
        """Placeholder — single agent execution today, multi-agent later."""
        return {
            "delegated_to": task.agent_id,
            "task_type": task.task_type,
            "status": "queued",
            "context_preview": ctx.memory_summary[:200],
            "multi_agent_ready": True,
        }


def get_runtime(user_id: int = 1) -> AgentRuntime:
    return AgentRuntime(user_id=user_id)
