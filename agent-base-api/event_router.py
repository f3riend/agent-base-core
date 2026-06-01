"""
Hybrid event routing — critical, creative, hybrid, analytical, monitoring.
"""

from __future__ import annotations

import os
from typing import Any

CRITICAL_EVENT_PREFIXES = (
    "stock.",
    "order.",
    "payment.",
    "inventory.",
    "shipping.",
    "fraud.",
    "risk.",
)

CRITICAL_EVENT_EXACT = {
    "store.rejected",
    "store.deleted",
    "item.deleted",
}

CREATIVE_EVENT_PREFIXES = (
    "product.",
    "banner.",
    "campaign.",
    "insight.",
    "review.",
    "customer.",
    "promotion.",
    "story.",     # Bölüm 6 — yeni
    "coupon.",    # Bölüm 6 — yeni
)

CREATIVE_STORE_EVENTS = {
    "store.created",
    "store.updated",
}

ANALYTICAL_EVENT_PREFIXES = (
    "sales.",
    "analytics.",
    "insight.",
    "metric.",
)

MONITORING_EVENT_PREFIXES = (
    "health.",
    "alert.",
    "monitor.",
    "system.",
)


def is_critical_event(event_name: str, event: dict | None = None) -> bool:
    if event_name in CRITICAL_EVENT_EXACT:
        return True
    for prefix in CRITICAL_EVENT_PREFIXES:
        if event_name.startswith(prefix):
            return True
    if event:
        payload = event.get("payload") or {}
        changes = event.get("changes") or {}
        if "stock" in changes or "payment" in str(payload).lower():
            return True
        if payload.get("fraud") or payload.get("risk_score"):
            return True
    return False


def is_creative_event(event_name: str) -> bool:
    for prefix in CREATIVE_EVENT_PREFIXES:
        if event_name.startswith(prefix):
            return True
    return event_name in CREATIVE_STORE_EVENTS


def is_analytical_event(event_name: str) -> bool:
    for prefix in ANALYTICAL_EVENT_PREFIXES:
        if event_name.startswith(prefix):
            return True
    return False


def is_monitoring_event(event_name: str) -> bool:
    for prefix in MONITORING_EVENT_PREFIXES:
        if event_name.startswith(prefix):
            return True
    return False


def route_event(event_name: str, event: dict | None = None) -> str:
    """
    Returns: critical | creative | hybrid | analytical | monitoring
    """
    if is_critical_event(event_name, event):
        return "critical"
    if is_creative_event(event_name):
        return "creative"
    if is_analytical_event(event_name):
        return "analytical"
    if is_monitoring_event(event_name):
        return "monitoring"
    return "hybrid"


def routing_confidence(event_name: str, event: dict | None = None) -> float:
    """How confident we are in route classification."""
    route = route_event(event_name, event)
    if route == "critical":
        return 0.95
    if route == "creative":
        return 0.88
    if route in ("analytical", "monitoring"):
        return 0.8
    return 0.55


def should_use_autonomous(
    event_name: str,
    event: dict | None,
    rules_matched: bool,
) -> bool:
    """Confidence-aware autonomous path decision."""
    route = route_event(event_name, event)
    if route == "critical":
        return False
    if rules_matched and route not in ("creative", "analytical"):
        return False
    if route in ("creative", "analytical", "hybrid"):
        return True
    if route == "monitoring":
        return os.environ.get("MONITORING_AUTONOMOUS", "0") == "1"
    return not rules_matched


def allow_mixed_execution(route: str) -> bool:
    """Hybrid: rules + autonomous supplement for analytical events."""
    return route in ("hybrid", "analytical") and os.environ.get(
        "MIXED_ORCHESTRATION", "0"
    ) == "1"
