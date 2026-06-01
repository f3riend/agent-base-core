"""
Unified business ontology — single source of truth for intent/domain/tool mapping.

This module replaces three previously-divergent maps:
    - autonomous_planner.INTENT_FROM_BI       (BI insight type → intent)
    - agent_registry._intent_to_domain         (intent → agent domain)
    - tool_registry.TOOL_METADATA              (tool → supported_business_intents)

Every intent declared here must have a domain. Every BI insight type that the
business_intelligence layer can emit must map to a known intent (or be marked
explicitly as no-route). The plan validator (plan_validator.py, Phase 5) uses
this registry to reject malformed planner output.

Adding a new business signal:
    1. Add the BI insight type to BI_INSIGHT_TO_INTENT.
    2. If the target intent does not exist yet, add it to INTENTS with its
       domain and default tools.
    3. Done — agent dispatch and tool ranking pick it up automatically.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class IntentSpec:
    name: str
    domain: str
    default_tools: tuple[str, ...]
    requires_approval: bool = False
    critical: bool = False
    description: str = ""


# Every intent the autonomous planner can settle on must live here.
# `domain` is matched against agent_registry domains.
# `default_tools` is the fallback when the planner doesn't pick tools.
_INTENT_SPECS: tuple[IntentSpec, ...] = (
    IntentSpec(
        name="low_stock_alert",
        domain="inventory",
        default_tools=("low_stock_notification_tool",),
        critical=True,
        description="Critical inventory alert (deterministic path)",
    ),
    IntentSpec(
        name="inventory_review",
        domain="inventory",
        default_tools=("low_stock_notification_tool",),
        description="Non-critical inventory analysis or restock planning",
    ),
    IntentSpec(
        name="discount_promotion",
        domain="marketing",
        default_tools=("coupon_generator_tool", "instagram_campaign_tool"),
        description="Discount-driven promotion (internal coupon + social draft)",
    ),
    IntentSpec(
        name="growth_marketing",
        domain="marketing",
        default_tools=("instagram_campaign_tool", "banner_generator_tool"),
        description="Growth campaign for viral / trending products",
    ),
    IntentSpec(
        name="marketing_campaign",
        domain="marketing",
        default_tools=("instagram_campaign_tool", "banner_generator_tool"),
        description="Generic marketing campaign (seasonal / brand)",
    ),
    IntentSpec(
        name="reputation",
        domain="support",
        default_tools=("support_response_tool", "faq_update_tool"),
        description="Reputation defence after negative signals",
    ),
    IntentSpec(
        name="customer_support",
        domain="support",
        default_tools=("support_response_tool",),
        description="Direct customer-question response",
    ),
    IntentSpec(
        name="shipping_response",
        domain="support",
        default_tools=("support_response_tool", "faq_update_tool"),
        description="Customer-facing response to shipping delays",
    ),
    IntentSpec(
        name="insights",
        domain="analytics",
        default_tools=("trend_analysis_tool",),
        description="Internal analysis / observation — no external publish",
    ),
    IntentSpec(
        name="store_welcome",
        domain="support",
        default_tools=("support_response_tool",),
        description="Welcome flow for newly onboarded stores",
    ),
    IntentSpec(
        name="general_marketing",
        domain="marketing",
        default_tools=("banner_generator_tool",),
        description="Catch-all when signals are weak",
    ),
)


INTENTS: dict[str, IntentSpec] = {spec.name: spec for spec in _INTENT_SPECS}


# Business intelligence insight type → canonical intent.
# Every key here MUST be a `type` produced by business_intelligence.analyze().
# Every value MUST be present in INTENTS (validated at import time).
BI_INSIGHT_TO_INTENT: dict[str, str] = {
    "campaign_opportunity":     "discount_promotion",
    "price_drop_promotion":     "discount_promotion",
    "viral_product":            "growth_marketing",
    "engagement_spike":         "growth_marketing",
    "customer_dissatisfaction": "reputation",
    "reputation_risk":          "reputation",
    "seasonal_opportunity":     "marketing_campaign",
    "sales_drop":               "insights",
    "repeat_failures":          "insights",
    "shipping_delay":           "shipping_response",   # was unmapped (CB-12)
    "inventory_risk":           "inventory_review",    # was unmapped (CB-12)
}


def is_known_intent(intent: str) -> bool:
    return intent in INTENTS


def intent_for_insight(insight_type: str, default: str = "general_marketing") -> str:
    return BI_INSIGHT_TO_INTENT.get(insight_type, default)


def domain_for_intent(intent: str, default: str = "marketing") -> str:
    spec = INTENTS.get(intent)
    return spec.domain if spec else default


def default_tools_for_intent(intent: str) -> list[str]:
    spec = INTENTS.get(intent)
    return list(spec.default_tools) if spec else []


def is_critical_intent(intent: str) -> bool:
    spec = INTENTS.get(intent)
    return bool(spec and spec.critical)


def intent_summary() -> list[dict]:
    """Inspection helper for the dashboard / API."""
    return [
        {
            "name": spec.name,
            "domain": spec.domain,
            "default_tools": list(spec.default_tools),
            "requires_approval": spec.requires_approval,
            "critical": spec.critical,
            "description": spec.description,
        }
        for spec in _INTENT_SPECS
    ]


# ---- Import-time validation: no dead entries, no missing targets. ----
_missing_intents = [
    (insight, intent)
    for insight, intent in BI_INSIGHT_TO_INTENT.items()
    if intent not in INTENTS
]
if _missing_intents:
    raise RuntimeError(
        f"ontology: BI_INSIGHT_TO_INTENT references unknown intents: {_missing_intents}"
    )
