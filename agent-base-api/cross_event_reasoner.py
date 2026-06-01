"""
Cross-event reasoning — temporal-weighted correlation across the timeline.

Returns hypotheses with causal structure (cause, effect, evidence) and a
business state transition label. Inputs are the recent N timeline events;
signals are counted with a temporal weight so a review from an hour ago
matters more than one from last week.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from db import execute_query
from observability import log_cross_event_reasoning
from timeline_service import fetch_timeline


# ----- temporal weighting -----


def _weight_for(ts_iso: str | None) -> float:
    """1.0 within last hour, 0.6 within a day, 0.2 within a week, 0.05 older."""
    if not ts_iso:
        return 0.4
    try:
        ts = datetime.fromisoformat(str(ts_iso).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return 0.4
    age = datetime.utcnow().replace(tzinfo=ts.tzinfo) - ts
    if age <= timedelta(hours=1):
        return 1.0
    if age <= timedelta(days=1):
        return 0.6
    if age <= timedelta(days=7):
        return 0.2
    return 0.05


# ----- signal extraction -----


_SIGNAL_KEYS = (
    "negative_reviews", "positive_reviews", "sales_drop",
    "shipping_delay", "discount", "campaign", "low_stock",
    "engagement_spike", "stock_recovery", "viral_signal",
)


def _fetch_recent_timeline(limit: int = 60) -> list[dict]:
    try:
        data = fetch_timeline(cursor=0, direction="desc", limit=limit)
        return data.get("data", [])
    except Exception:
        return []


def _weighted_signals(events: list[dict]) -> dict[str, float]:
    counts = {k: 0.0 for k in _SIGNAL_KEYS}
    for ev in events:
        group = ev.get("group", "")
        event_name = ev.get("event", "")
        payload = ev.get("payload") or {}
        ts = ev.get("ts") or ev.get("created_at")
        w = _weight_for(ts)

        if group == "review" or "review" in str(ev.get("description", "")):
            if event_name == "negative" or payload.get("rating", 5) <= 2:
                counts["negative_reviews"] += w
            else:
                counts["positive_reviews"] += w

        if group == "sales" or payload.get("sales_change_pct", 0) < -10:
            counts["sales_drop"] += w
        if payload.get("sales_change_pct", 0) > 15:
            counts["viral_signal"] += w * 0.6

        if group == "shipping" or payload.get("shipping_delay") or payload.get("delay_days"):
            counts["shipping_delay"] += w

        if payload.get("discount") or payload.get("discount_percent"):
            counts["discount"] += w
        if group == "campaign":
            counts["campaign"] += w

        if group == "stock":
            new_stock = payload.get("new_stock")
            if new_stock is not None and new_stock < 10:
                counts["low_stock"] += w
            elif new_stock is not None and new_stock > 30:
                counts["stock_recovery"] += w * 0.5

        ctr = payload.get("ctr") or payload.get("click_rate") or 0
        if ctr > 0.08:
            counts["engagement_spike"] += w

    return counts


# ----- causal hypotheses -----


def _build_hypotheses(signals: dict[str, float]) -> list[dict]:
    hyps: list[dict] = []

    def has(name: str, threshold: float = 0.8) -> bool:
        return signals.get(name, 0) >= threshold

    if has("sales_drop") and has("negative_reviews"):
        hyps.append({
            "type": "customer_dissatisfaction",
            "message": "Satış düşüşü + olumsuz yorumlar → müşteri memnuniyetsizliği olası",
            "strength": min(1.0, 0.6 + 0.15 * (signals["sales_drop"] + signals["negative_reviews"])),
            "evidence": ["sales_drop", "negative_reviews"],
            "state_transition": "deteriorating",
        })

    if has("shipping_delay") and has("negative_reviews"):
        hyps.append({
            "type": "delivery_experience_issue",
            "message": "Kargo gecikmesi + şikayetler → teslimat deneyimi sorunu",
            "strength": min(1.0, 0.6 + 0.18 * (signals["shipping_delay"] + signals["negative_reviews"])),
            "evidence": ["shipping_delay", "negative_reviews"],
            "state_transition": "deteriorating",
        })

    if has("engagement_spike") and has("campaign"):
        hyps.append({
            "type": "campaign_momentum",
            "message": "Kampanya + yüksek etkileşim → büyüme fırsatı",
            "strength": min(1.0, 0.55 + 0.2 * signals["engagement_spike"]),
            "evidence": ["engagement_spike", "campaign"],
            "state_transition": "improving",
        })

    if has("discount", 1.5) and not has("sales_drop"):
        hyps.append({
            "type": "promotion_working",
            "message": "İndirim aktivitesi yoğun ve satışlar düşmüyor — promosyon etkili",
            "strength": 0.7,
            "evidence": ["discount"],
            "state_transition": "improving",
        })

    if has("low_stock", 1.5):
        hyps.append({
            "type": "supply_chain_pressure",
            "message": "Birden fazla stok olayı — tedarik baskısı",
            "strength": min(1.0, 0.55 + 0.12 * signals["low_stock"]),
            "evidence": ["low_stock"],
            "state_transition": "concerning",
        })

    if has("viral_signal"):
        hyps.append({
            "type": "growth_window",
            "message": "Satış ivmesi yükseliyor — büyüme penceresi açık",
            "strength": min(1.0, 0.55 + 0.2 * signals["viral_signal"]),
            "evidence": ["viral_signal"],
            "state_transition": "improving",
        })

    if has("positive_reviews", 2.0) and not has("negative_reviews"):
        hyps.append({
            "type": "reputation_strength",
            "message": "Olumlu yorum yoğunluğu — itibar güçlü",
            "strength": min(1.0, 0.4 + 0.1 * signals["positive_reviews"]),
            "evidence": ["positive_reviews"],
            "state_transition": "stable",
        })

    if not hyps:
        hyps.append({
            "type": "stable_operations",
            "message": "Belirgin çapraz olay korelasyonu yok — operasyonlar stabil görünüyor",
            "strength": 0.4,
            "evidence": [],
            "state_transition": "stable",
        })

    return hyps


def _state_transition(hypotheses: list[dict]) -> str:
    """Aggregate per-hypothesis transitions into a single label."""
    if not hypotheses:
        return "stable"
    weight = {"deteriorating": -2, "concerning": -1, "stable": 0, "improving": 1}
    total = 0.0
    for h in hypotheses:
        total += weight.get(h.get("state_transition", "stable"), 0) * h.get("strength", 0.5)
    if total <= -1.0:
        return "deteriorating"
    if total <= -0.3:
        return "concerning"
    if total >= 0.6:
        return "improving"
    return "stable"


# ----- public API -----


def reason_across_events(
    user_id: int = 1,
    current_event: dict | None = None,
    limit: int = 60,
) -> dict[str, Any]:
    events = _fetch_recent_timeline(limit)
    signals = _weighted_signals(events)
    hypotheses = _build_hypotheses(signals)
    top = max(hypotheses, key=lambda h: h.get("strength", 0))
    transition = _state_transition(hypotheses)

    # Preserve the count-shaped signal_counts key for downstream consumers
    # that may still index it; the values are temporal-weighted floats now.
    result = {
        "signal_counts": {k: round(v, 2) for k, v in signals.items()},
        "hypotheses": hypotheses,
        "primary_hypothesis": top,
        "summary": top["message"],
        "confidence": round(min(0.95, top.get("strength", 0.5)), 3),
        "event_sample_size": len(events),
        "business_state_transition": transition,
    }

    log_cross_event_reasoning(
        top["type"],
        result["summary"],
        result["confidence"],
        signals=result["signal_counts"],
        user_id=user_id,
    )
    return result
