"""
Context compression for planner and business chat — reduce prompt size.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any


def compress_timeline(events: list[dict], max_items: int = 12) -> str:
    if not events:
        return "Zaman tüneli boş."
    lines = []
    for ev in events[:max_items]:
        lines.append(
            f"#{ev.get('id')} {ev.get('group')}.{ev.get('event')}: "
            f"{(ev.get('description') or '')[:80]}"
        )
    groups = Counter(e.get("group") for e in events)
    lines.append(f"Özet gruplar: {dict(groups)}")
    return "\n".join(lines)


def compress_workflow_history(workflows: list[dict], max_items: int = 8) -> str:
    if not workflows:
        return "İş akışı geçmişi yok."
    lines = []
    for w in workflows[:max_items]:
        lines.append(
            f"{w.get('workflow_name')} ({w.get('status')}) "
            f"entity={w.get('entity_type')}#{w.get('entity_id')}"
        )
    return "\n".join(lines)


def compress_repetitive_events(events: list[dict]) -> list[dict]:
    """Collapse duplicate event types keeping latest."""
    seen = {}
    for ev in reversed(events):
        key = f"{ev.get('group')}.{ev.get('event')}"
        if key not in seen:
            seen[key] = ev
    return list(reversed(list(seen.values())))


def build_business_summary(
    state: dict,
    insights: list[dict],
    cross_reasoning: dict | None = None,
) -> str:
    parts = []
    inv = state.get("inventory", {})
    sales = state.get("sales", {})
    parts.append(f"Stok: {inv.get('health', '?')} ({inv.get('low_stock_count', 0)} düşük)")
    parts.append(f"Toplam satış birimi: {sales.get('total_item_sales', 0)}")
    if sales.get("top_products"):
        top = sales["top_products"][0]
        parts.append(f"Lider ürün: {top.get('name')} ({top.get('sales')} satış)")
    if insights:
        parts.append(f"İçgörü: {insights[0].get('message', insights[0]) if isinstance(insights[0], dict) else insights[0]}")
    if cross_reasoning:
        parts.append(f"Çapraz analiz: {cross_reasoning.get('summary', '')}")
    return "\n".join(parts)


def compress_for_planner(ctx: dict) -> dict:
    """Return smaller context blob for LLM."""
    events = (ctx.get("event") or {})
    memory = ctx.get("memory", {})
    return {
        "event_summary": json.dumps(events, default=str)[:800],
        "business_state_summary": ctx.get("business_state_summary", "")[:600],
        "bi_summary": (ctx.get("business_intelligence") or {}).get("summary", "")[:400],
        "memory_recent": memory.get("recent_decisions", [])[:5],
        "cross_event": (ctx.get("cross_event_reasoning") or {}).get("summary", "")[:300],
    }
