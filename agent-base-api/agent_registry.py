"""
Multi-agent preparation — specialized agent roles for future orchestration.
Not fully implemented; provides clean abstractions for planner/runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class AgentSpec:
    id: str
    name: str
    domain: str
    description: str
    supported_routes: tuple[str, ...]
    tool_categories: tuple[str, ...]
    priority: int = 50


AGENT_REGISTRY: list[AgentSpec] = [
    AgentSpec(
        id="marketing",
        name="Marketing Agent",
        domain="marketing",
        description="Kampanya, sosyal medya, banner ve promosyon planlama",
        supported_routes=("creative", "hybrid"),
        tool_categories=("marketing",),
        priority=80,
    ),
    AgentSpec(
        id="analytics",
        name="Analytics Agent",
        domain="analytics",
        description="Trend analizi, satış içgörüleri, performans",
        supported_routes=("analytical", "monitoring", "hybrid"),
        tool_categories=("analytics", "marketing"),
        priority=70,
    ),
    AgentSpec(
        id="support",
        name="Support Agent",
        domain="support",
        description="Müşteri soruları, yorumlar, destek kampanyaları",
        supported_routes=("creative", "hybrid"),
        tool_categories=("support", "marketing"),
        priority=75,
    ),
    AgentSpec(
        id="inventory",
        name="Inventory Agent",
        domain="inventory",
        description="Stok, sipariş, tedarik — yalnızca kritik deterministik yol",
        supported_routes=("critical",),
        tool_categories=("inventory",),
        priority=95,
    ),
    AgentSpec(
        id="executive",
        name="Executive Agent",
        domain="executive",
        description="Üst düzey iş kararları ve onay önerileri",
        supported_routes=("analytical", "monitoring", "hybrid"),
        tool_categories=("marketing", "analytics"),
        priority=60,
    ),
]


def get_agent_for_route(route: str) -> AgentSpec | None:
    for agent in sorted(AGENT_REGISTRY, key=lambda a: -a.priority):
        if route in agent.supported_routes:
            return agent
    return AGENT_REGISTRY[0] if AGENT_REGISTRY else None


def get_agent_for_domain(domain: str) -> AgentSpec | None:
    for agent in AGENT_REGISTRY:
        if agent.domain == domain:
            return agent
    return None


def agent_context_for_plan(route: str, business_intent: str) -> dict[str, Any]:
    """Metadata passed to planner for future multi-agent routing."""
    agent = get_agent_for_route(route)
    domain_agent = get_agent_for_domain(
        _intent_to_domain(business_intent)
    )
    selected = domain_agent or agent
    return {
        "agent_id": selected.id if selected else "marketing",
        "agent_name": selected.name if selected else "Marketing Agent",
        "agent_domain": selected.domain if selected else "marketing",
        "multi_agent_ready": True,
    }


def _intent_to_domain(intent: str) -> str:
    """Single source: ontology.domain_for_intent. Kept as a thin wrapper so
    older call sites that imported _intent_to_domain continue to work."""
    from ontology import domain_for_intent
    return domain_for_intent(intent, default="marketing")


def list_agents() -> list[dict]:
    return [
        {
            "id": a.id,
            "name": a.name,
            "domain": a.domain,
            "description": a.description,
            "routes": list(a.supported_routes),
            "tool_categories": list(a.tool_categories),
        }
        for a in AGENT_REGISTRY
    ]
