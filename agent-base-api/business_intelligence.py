"""
Business Intelligence Layer — trend and opportunity detection for autonomous planning.
"""

from __future__ import annotations

import json
from typing import Any

from db import execute_query
from resource_service import fetch_item, fetch_store


def _safe_float(val, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _event_month(event: dict) -> int:
    """Return the month of the event, replay-safe.

    Preferred sources, in order:
        1. event["ts"]                  (timeline row timestamp)
        2. event["created_at"]          (some shapes use this)
        3. event["payload"]["ts"]
        4. utcnow() as last resort

    Using the event's own timestamp means replaying a historical event
    produces the same insights regardless of wall-clock time — see CB-11.
    """
    from datetime import datetime

    for key in ("ts", "created_at"):
        v = event.get(key)
        if v:
            try:
                return datetime.fromisoformat(str(v).replace("Z", "+00:00")).month
            except (ValueError, TypeError):
                continue
    payload = event.get("payload") or {}
    ts = payload.get("ts") or payload.get("created_at")
    if ts:
        try:
            return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).month
        except (ValueError, TypeError):
            pass
    return datetime.utcnow().month


def _analyze_event_payload(event: dict) -> list[dict]:
    """Infer signals from structured payload/changes without keyword regex."""
    insights = []
    payload = event.get("payload") or {}
    changes = event.get("changes") or {}

    discount = None
    for key in ("discount", "discount_percent", "indirim_orani"):
        if key in payload:
            discount = _safe_float(payload[key])

    if "discount" in changes:
        discount = _safe_float(changes["discount"].get("to"), discount)

    if discount and discount >= 10:
        insights.append({
            "type": "campaign_opportunity",
            "strength": min(1.0, discount / 100 + 0.4),
            "message": f"İndirim fırsatı tespit edildi (%{discount})",
            "data": {"discount_pct": discount},
        })

    if "price" in changes:
        old_p = _safe_float(changes["price"].get("from"))
        new_p = _safe_float(changes["price"].get("to"))
        if old_p > 0 and new_p < old_p * 0.9:
            drop_pct = (old_p - new_p) / old_p * 100
            insights.append({
                "type": "price_drop_promotion",
                "strength": min(1.0, drop_pct / 50),
                "message": f"Fiyat düşüşü %{drop_pct:.0f} — promosyon önerilir",
                "data": {"from": old_p, "to": new_p},
            })

    if "stock" in changes:
        new_stock = _safe_float(changes["stock"].get("to"))
        if new_stock < 10:
            insights.append({
                "type": "inventory_risk",
                "strength": 0.95,
                "message": f"Stok riski: {new_stock} adet",
                "data": {"stock": new_stock},
                "critical": True,
            })

    rating = payload.get("rating") or payload.get("score")
    if rating is not None and _safe_float(rating) <= 2:
        insights.append({
            "type": "customer_dissatisfaction",
            "strength": 0.88,
            "message": "Düşük puanlı müşteri geri bildirimi",
            "data": {"rating": rating},
        })

    sentiment = str(payload.get("sentiment", "")).lower()
    if sentiment in ("negative", "bad", "olumsuz"):
        insights.append({
            "type": "reputation_risk",
            "strength": 0.85,
            "message": "Olumsuz müşteri duyarlılığı",
            "data": payload,
        })

    if payload.get("viral") or payload.get("trending"):
        insights.append({
            "type": "viral_product",
            "strength": 0.9,
            "message": "Viral ürün sinyali",
            "data": payload,
        })

    delay = payload.get("delay_days") or payload.get("shipping_delay")
    if delay:
        insights.append({
            "type": "shipping_delay",
            "strength": 0.92,
            "message": "Kargo gecikmesi tespit edildi",
            "data": {"delay": delay},
            "critical": True,
        })

    ctr = _safe_float(payload.get("ctr") or payload.get("click_rate"))
    if ctr > 0.08:
        insights.append({
            "type": "engagement_spike",
            "strength": min(1.0, ctr * 5),
            "message": f"Banner etkileşim artışı (CTR {ctr:.2%})",
            "data": {"ctr": ctr},
        })

    nl = str(payload.get("natural_language", "")).lower()
    if nl:
        if any(w in nl for w in ("indirim", "discount", "kampanya", "promo")):
            insights.append({
                "type": "campaign_opportunity",
                "strength": 0.78,
                "message": "Kullanıcı niyeti: promosyon / indirim",
                "data": {"source": "natural_language"},
            })
        if any(w in nl for w in ("instagram", "paylaşım", "sosyal", "social")):
            insights.append({
                "type": "campaign_opportunity",
                "strength": 0.82,
                "message": "Kullanıcı niyeti: sosyal medya içeriği",
                "data": {"source": "natural_language"},
            })
        if any(w in nl for w in ("satış", "sales", "düş", "drop")):
            insights.append({
                "type": "sales_drop",
                "strength": 0.7,
                "message": "Kullanıcı niyeti: satış bildirimi",
                "data": {"source": "natural_language"},
            })
        if any(w in nl for w in ("yorum", "negatif", "şikayet", "review")):
            insights.append({
                "type": "customer_dissatisfaction",
                "strength": 0.75,
                "message": "Kullanıcı niyeti: müşteri memnuniyeti",
                "data": {"source": "natural_language"},
            })

    sales_change = payload.get("sales_change_pct")
    if sales_change is not None and _safe_float(sales_change) < -15:
        insights.append({
            "type": "sales_drop",
            "strength": 0.8,
            "message": f"Satış düşüşü %{abs(_safe_float(sales_change)):.0f}",
            "data": {"sales_change_pct": sales_change},
        })

    return insights


def _analyze_store_catalog(user_id: int, store_id: int | None) -> list[dict]:
    insights = []
    if not store_id:
        return insights

    items = execute_query(
        """
        SELECT id, name, stock, sales, price
        FROM items WHERE store_id=?
        ORDER BY sales DESC
        """,
        (store_id,),
    )
    if not items:
        return insights

    total_sales = sum(r["sales"] or 0 for r in items)
    low_stock = [r for r in items if (r["stock"] or 0) < 10]

    if low_stock:
        insights.append({
            "type": "inventory_risk",
            "strength": 0.75,
            "message": f"{len(low_stock)} ürün düşük stokta",
            "data": {"count": len(low_stock)},
        })

    if items and total_sales > 0:
        top = items[0]
        share = (top["sales"] or 0) / total_sales
        if share > 0.5 and (top["sales"] or 0) > 20:
            insights.append({
                "type": "viral_product",
                "strength": 0.7,
                "message": f"Trend ürün: {top['name']}",
                "data": {"item_id": top["id"], "sales": top["sales"]},
            })

    return insights


def _analyze_automation_patterns(user_id: int) -> list[dict]:
    insights = []
    rows = execute_query(
        """
        SELECT rule_name, execution_status, COUNT(*) as cnt
        FROM automation_logs
        WHERE user_id=?
        GROUP BY rule_name, execution_status
        ORDER BY cnt DESC LIMIT 20
        """,
        (user_id,),
    )
    failed = sum(r["cnt"] for r in rows if r["execution_status"] == "failed")
    if failed >= 3:
        insights.append({
            "type": "repeat_failures",
            "strength": 0.7,
            "message": f"Tekrarlayan otomasyon hataları ({failed})",
            "data": {"failed_count": failed},
        })
    return insights


def analyze(
    event: dict,
    event_name: str,
    context: dict,
    user_id: int = 1,
) -> dict:
    """
    Full BI snapshot for planner consumption.
    Returns insights list + summary scores by category.
    """
    insights = _analyze_event_payload(event)
    insights.extend(_analyze_automation_patterns(user_id))

    store = context.get("store") or {}
    item = context.get("item") or {}
    store_id = store.get("id") or item.get("store_id")
    insights.extend(_analyze_store_catalog(user_id, store_id))

    group = event.get("group", "")
    ev = event.get("event", "")
    if group == "review" or "review" in event_name:
        insights.append({
            "type": "customer_dissatisfaction" if ev == "negative" else "engagement_spike",
            "strength": 0.75,
            "message": f"Müşteri yorumu olayı: {event_name}",
            "data": event.get("payload") or {},
        })

    if group == "campaign" or "campaign" in event_name:
        insights.append({
            "type": "campaign_opportunity",
            "strength": 0.8,
            "message": "Kampanya aktivitesi",
            "data": event.get("payload") or {},
        })

    # Seasonal heuristic — replay-safe: read month from the event timestamp
    # if available, falling back to wall-clock only as a last resort.
    # The bug (CB-11) was: utcnow().month meant replaying an old event in
    # a different month produced a different insight → different plan.
    month = _event_month(event)
    if month in (11, 12, 1):
        insights.append({
            "type": "seasonal_opportunity",
            "strength": 0.55,
            "message": "Sezonsal satış dönemi (kış/yılbaşı)",
            "data": {"month": month, "source": "event_ts" if event.get("ts") else "wall_clock"},
        })

    by_type: dict[str, float] = {}
    for ins in insights:
        t = ins["type"]
        by_type[t] = max(by_type.get(t, 0), ins["strength"])

    creative_types = {
        "campaign_opportunity", "price_drop_promotion", "viral_product",
        "engagement_spike", "seasonal_opportunity", "reputation_risk",
        "customer_dissatisfaction",
    }
    critical_types = {"inventory_risk", "shipping_delay", "sales_drop"}

    return {
        "insights": insights,
        "scores": by_type,
        "has_critical": any(i.get("critical") for i in insights),
        "has_creative": any(i["type"] in creative_types for i in insights),
        "top_opportunity": max(insights, key=lambda x: x["strength"]) if insights else None,
        "summary": _build_summary(insights),
    }


def _build_summary(insights: list[dict]) -> str:
    if not insights:
        return "Belirgin iş sinyali yok"
    top = sorted(insights, key=lambda x: -x["strength"])[:3]
    return "; ".join(i["message"] for i in top)


def detect_trending_products(user_id: int = 1, limit: int = 5) -> list[dict]:
    """Viral/trending detection from sales velocity and engagement."""
    items = execute_query(
        """
        SELECT id, name, sales, stock, price, category
        FROM items ORDER BY sales DESC LIMIT 30
        """
    )
    if not items:
        return []

    trending = []
    max_sales = max((r["sales"] or 0) for r in items) or 1
    for r in items:
        sales = r["sales"] or 0
        if sales < 1:
            continue
        velocity = sales / max_sales
        if velocity > 0.2 or sales > 5:
            trending.append({
                "id": r["id"],
                "name": r["name"],
                "sales": sales,
                "score": round(velocity, 2),
                "image": f"https://placehold.co/120x120/png?text={r['name'][:8]}",
                "reason": "Yüksek satış hızı" if velocity > 0.6 else "Güçlü satış",
            })
    return sorted(trending, key=lambda x: -x["score"])[:limit]


def analyze_sales_trend(user_id: int = 1) -> dict:
    items = execute_query(
        "SELECT sales, price FROM items"
    )
    total = sum(r["sales"] or 0 for r in items)
    avg_price = sum(r["price"] or 0 for r in items) / max(len(items), 1)
    return {
        "total_sales": total,
        "avg_price": round(avg_price, 2),
        "trend": "down" if total < 100 else "up",
        "message": (
            f"Toplam {total} satış — "
            + ("düşüş riski: promosyon önerilir" if total < 50 else "sağlıklı hacim")
        ),
    }


def generate_human_insights(
    user_id: int,
    state: dict,
    events: list[dict],
    cross: dict,
    bi: dict,
) -> list[str]:
    """Human-readable insight strings for chat and dashboard."""
    lines = []
    sales = analyze_sales_trend(user_id)
    lines.append(sales["message"])

    trending = detect_trending_products(user_id, 3)
    if trending:
        lines.append(
            "Trend ürünler: " + ", ".join(f"{t['name']} ({t['sales']})" for t in trending)
        )

    if cross.get("summary"):
        lines.append(cross["summary"])

    for ins in (bi.get("insights") or [])[:4]:
        lines.append(ins.get("message", str(ins)))

    neg = state.get("engagement", {}).get("negative_reviews", 0)
    if neg > 3:
        lines.append(f"Dikkat: {neg} olumsuz yorum kayıtlı — destek kampanyası düşünün.")

    inv = state.get("inventory", {}).get("health")
    if inv == "critical":
        lines.append("Stok sağlığı kritik — envanter kurallarını kontrol edin.")

    camps = state.get("campaigns", {}).get("active_count", 0)
    if camps:
        lines.append(f"{camps} aktif kampanya iş akışı çalışıyor.")

    return list(dict.fromkeys(lines))[:10]


def get_insights_for_api(user_id: int = 1, limit: int = 20) -> list[dict]:
    """Recent BI-style rows from planner memory + workflows for dashboard."""
    rows = execute_query(
        """
        SELECT workflow_name, reason, confidence, outcome, created_at, plan_json
        FROM planner_memory
        WHERE user_id=?
        ORDER BY id DESC LIMIT ?
        """,
        (user_id, limit),
    )
    result = []
    for r in rows:
        plan = json.loads(r["plan_json"] or "{}")
        result.append({
            "workflow": r["workflow_name"],
            "reason": r["reason"],
            "confidence": r["confidence"],
            "outcome": r["outcome"],
            "business_intent": plan.get("business_intent"),
            "created_at": r["created_at"],
        })
    return result
