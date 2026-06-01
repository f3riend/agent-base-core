"""
Dynamic tool registry with semantic capability scoring and reliability tracking.
"""

from __future__ import annotations

import json
import re
from typing import Any

from db import execute_query

TOOL_METADATA: list[dict[str, Any]] = [
    {
        "name": "instagram_campaign_tool",
        "category": "marketing",
        "description": "Instagram kampanyası — dış sosyal yayın (onay gerekir).",
        "capabilities": ["social_post_draft", "caption_generation", "hashtag_suggestions"],
        "supported_business_intents": [
            "promotion", "discount", "social_media", "instagram", "campaign",
        ],
        "risk_level": "medium",
        "requires_approval": True,
        "success_rate": 0.92,
        "entity_types": ["store", "item"],
    },
    {
        "name": "banner_generator_tool",
        "category": "marketing",
        "description": "Banner metni ve tasarım özeti — dahili.",
        "capabilities": ["banner_copy", "promotional_headline", "cta_generation"],
        "supported_business_intents": ["banner", "promotion", "campaign", "discount"],
        "risk_level": "low",
        "requires_approval": False,
        "success_rate": 0.88,
        "entity_types": ["store", "item", "banner"],
    },
    {
        "name": "faq_update_tool",
        "category": "support",
        "description": "SSS güncelleme — dahili, onay gerekmez.",
        "capabilities": ["faq_generation", "knowledge_base"],
        "supported_business_intents": ["customer_engagement", "faq", "support"],
        "risk_level": "low",
        "requires_approval": False,
        "success_rate": 0.9,
        "entity_types": ["store"],
    },
    {
        "name": "coupon_generator_tool",
        "category": "marketing",
        "description": "Kupon kodu üretimi — dahili taslak.",
        "capabilities": ["coupon_code", "discount_logic"],
        "supported_business_intents": ["promotion", "discount", "campaign"],
        "risk_level": "low",
        "requires_approval": False,
        "success_rate": 0.86,
        "entity_types": ["item", "store"],
    },
    {
        "name": "support_response_tool",
        "category": "support",
        "description": "Müşteri destek yanıt taslağı.",
        "capabilities": ["reply_draft", "sentiment_response"],
        "supported_business_intents": ["reputation", "customer_engagement", "support"],
        "risk_level": "low",
        "requires_approval": False,
        "success_rate": 0.87,
        "entity_types": ["store"],
    },
    {
        "name": "trend_analysis_tool",
        "category": "analytics",
        "description": "Satış ve trend analizi özeti.",
        "capabilities": ["trend_detection", "sales_summary", "recommendations"],
        "supported_business_intents": ["insights", "growth_marketing", "analytics"],
        "risk_level": "low",
        "requires_approval": False,
        "success_rate": 0.91,
        "entity_types": ["store"],
    },
    {
        "name": "low_stock_notification_tool",
        "category": "inventory",
        "description": "Düşük stok uyarısı (kritik yol).",
        "capabilities": ["inventory_alert"],
        "supported_business_intents": ["low_stock", "inventory_risk"],
        "risk_level": "low",
        "requires_approval": False,
        "success_rate": 0.95,
        "entity_types": ["item"],
        "critical_only": True,
    },
]

CRITICAL_TASK_MAP = {
    "welcome_instagram_post": {
        "task_type": "instagram_launch_campaign",
        "tools": ["instagram_campaign_tool"],
    },
    "low_stock_alert": {
        "task_type": "low_stock_analysis",
        "tools": ["low_stock_notification_tool"],
    },
}


def _tokenize(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9ğüşıöçâ]+", text.lower()) if len(w) > 2}


def _load_success_rates() -> dict[str, float]:
    rates: dict[str, float] = {}
    try:
        rows = execute_query(
            """
            SELECT te.tool_name, te.status, COUNT(*) as cnt
            FROM tool_executions te
            GROUP BY te.tool_name, te.status
            """
        )
        stats: dict[str, dict] = {}
        for r in rows:
            name = r["tool_name"]
            if name not in stats:
                stats[name] = {"success": 0, "total": 0}
            stats[name]["total"] += r["cnt"]
            if r["status"] == "success":
                stats[name]["success"] += r["cnt"]
        for name, s in stats.items():
            if s["total"] > 0:
                rates[name] = round(s["success"] / s["total"], 3)
    except Exception:
        pass
    return rates


def get_enriched_metadata() -> list[dict]:
    live_rates = _load_success_rates()
    enriched = []
    for meta in TOOL_METADATA:
        m = dict(meta)
        m["success_rate"] = live_rates.get(m["name"], m.get("success_rate", 0.85))
        enriched.append(m)
    return enriched


def get_all_metadata() -> list[dict]:
    return get_enriched_metadata()


def get_metadata(tool_name: str) -> dict | None:
    for meta in get_enriched_metadata():
        if meta["name"] == tool_name:
            return meta
    return None


def tool_requires_approval(tool_name: str) -> bool:
    meta = get_metadata(tool_name)
    return bool(meta and meta.get("requires_approval"))


def get_marketing_tools() -> list[dict]:
    return [
        m for m in get_enriched_metadata()
        if m.get("category") == "marketing" and not m.get("critical_only")
    ]


def _semantic_score(meta: dict, context_tokens: set[str], intent: str) -> float:
    score = 0.0
    cap_text = " ".join(meta.get("capabilities", []))
    desc = meta.get("description", "")
    intents = meta.get("supported_business_intents", [])
    blob = _tokenize(f"{cap_text} {desc} {' '.join(intents)}")
    overlap = len(context_tokens & blob)
    score += overlap * 0.35
    if intent:
        if intent.replace("_", " ") in desc or intent in intents:
            score += 1.2
    score += meta.get("success_rate", 0.8) * 0.4
    return score


def rank_tools_for_context(
    context_text: str,
    business_intent: str = "",
    limit: int = 3,
    exclude_critical_only: bool = True,
) -> list[tuple[str, float]]:
    tokens = _tokenize(context_text)
    scores: list[tuple[float, str]] = []
    for meta in get_enriched_metadata():
        if exclude_critical_only and meta.get("critical_only"):
            continue
        s = _semantic_score(meta, tokens, business_intent)
        if s > 0.2:
            scores.append((s, meta["name"]))
    scores.sort(key=lambda x: -x[0])
    return scores[:limit]


def get_tools_for_intent(text: str, limit: int = 3) -> list[str]:
    ranked = rank_tools_for_context(text, limit=limit)
    return [name for _, name in ranked]


def get_tools_for_autonomous_plan(plan: dict) -> list[str]:
    if plan.get("tools"):
        return [t for t in plan["tools"] if get_metadata(t)]
    text = " ".join([
        plan.get("workflow_name", ""),
        plan.get("reason", ""),
        plan.get("business_intent", ""),
    ])
    ranked = rank_tools_for_context(text, plan.get("business_intent", ""), limit=4)
    return [n for _, n in ranked] or ["banner_generator_tool"]


def rank_tools_with_reasoning(context_text: str, business_intent: str, limit: int = 3) -> dict:
    ranked = rank_tools_for_context(context_text, business_intent, limit)
    return {
        "tools": [n for _, n in ranked],
        "scores": {n: round(s, 3) for s, n in ranked},
        "reasoning": f"'{business_intent}' için {len(ranked)} araç seçildi",
    }


def resolve_tool_instances(tool_names: list[str]) -> list:
    from tools import TOOLS
    instances = []
    for name in tool_names:
        if name in TOOLS:
            instances.append(TOOLS[name])
    return instances


def build_registry_summary() -> str:
    lines = []
    for meta in get_enriched_metadata():
        caps = ", ".join(meta.get("capabilities", [])[:4])
        lines.append(
            f"- {meta['name']} ({meta['category']}, onay={meta['requires_approval']}): "
            f"{meta['description']} [{caps}]"
        )
    return "\n".join(lines)


def get_registry_for_api() -> list[dict]:
    return [
        {
            "name": m["name"],
            "category": m["category"],
            "description": m["description"],
            "capabilities": m.get("capabilities", []),
            "supported_business_intents": m.get("supported_business_intents", []),
            "risk_level": m.get("risk_level", "low"),
            "requires_approval": m.get("requires_approval", False),
            "success_rate": m.get("success_rate", 0.85),
        }
        for m in get_enriched_metadata()
    ]
