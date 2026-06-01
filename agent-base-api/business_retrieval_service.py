"""
Business retrieval service — typed data retrievers behind the AI chat.

When the user asks "Hangi ürün en çok stokta?" the system must return a
real answer about the actual highest-stock product, not a generic state
summary. This module owns the data-grounded retrievers; the query router
(business_query_router.py) maps user intent to one of these functions.

Every retriever returns a structured response:
    {
      "intent": str,           # canonical intent name
      "answer": str,           # operator-tone Turkish narrative
      "data": dict | list,     # the underlying facts shown
      "recommendations": list, # ontology-filtered suggested intents
      "confidence": float,     # 0..1
    }

The `data` field is shown verbatim in the dashboard's chat panel so the
operator can see WHAT facts the answer is grounded in.

Retrievers never fabricate. If there's no data they say so explicitly.
"""

from __future__ import annotations

import json
import sqlite3
import os
from datetime import datetime, timedelta
from typing import Any

from db import execute_query
from ontology import INTENTS, default_tools_for_intent


# ---------------------------------------------------------------------------
# Fake-platform DB helpers
# ---------------------------------------------------------------------------


_FAKE_DB_PATH = os.environ.get("FAKE_API_DB_PATH", "fake_ai_api.db")


def _fake_query(sql: str, params: tuple = ()) -> list[dict]:
    """Read-only query against fake_ai_api.db. Returns list of dicts."""
    try:
        conn = sqlite3.connect(_FAKE_DB_PATH, timeout=15)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except sqlite3.Error as exc:
        print(f"[RETRIEVAL] fake_query error: {exc}")
        return []


# ---------------------------------------------------------------------------
# Recommendation helpers
# ---------------------------------------------------------------------------


_INTENT_SUGGESTIONS: dict[str, str] = {
    "discount_promotion":   "küçük bir indirim kampanyası",
    "growth_marketing":     "büyütme odaklı bir Instagram paylaşımı",
    "marketing_campaign":   "yeni bir kampanya akışı",
    "reputation":           "açıklayıcı bir paylaşım ve destek akışı",
    "shipping_response":    "kargo süreci için bir bilgilendirme",
    "customer_support":     "bekleyen müşteri sorularına bir cevap akışı",
    "inventory_review":     "düşük stoklu ürünler için bir hatırlatma",
    "insights":             "kısa bir trend analizi",
}


def _recs(intents: list[str]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for name in intents:
        if name in seen or name not in INTENTS:
            continue
        seen.add(name)
        out.append({
            "intent": name,
            "suggestion": _INTENT_SUGGESTIONS.get(name, INTENTS[name].description or name),
            "default_tools": default_tools_for_intent(name),
        })
    return out[:4]


def _empty(reason: str) -> dict:
    return {
        "intent": "no_data",
        "answer": (
            "Bu soruyu cevaplayabilmem için yeterli veri yok. "
            f"({reason})\n\n"
            "Birkaç test olayı veya seed verisi oluşturduktan sonra tekrar sorabilirsin."
        ),
        "data": {},
        "recommendations": [],
        "confidence": 0.3,
    }


# ---------------------------------------------------------------------------
# Stock retrievers
# ---------------------------------------------------------------------------


def top_stock_product(user_id: int = 1, store_id: int | None = None) -> dict:
    """Highest-stock product right now."""
    sql = """
        SELECT i.id, i.name, i.stock, i.price, i.sales, i.category, s.name AS store_name
        FROM items i LEFT JOIN stores s ON s.id = i.store_id
        WHERE i.stock IS NOT NULL
    """
    params: list = []
    if store_id is not None:
        sql += " AND i.store_id = ?"
        params.append(store_id)
    sql += " ORDER BY i.stock DESC LIMIT 1"

    rows = _fake_query(sql, tuple(params))
    if not rows:
        return _empty("ürün katalogu boş görünüyor")

    top = rows[0]
    sales = top["sales"] or 0
    parts = [
        f"Şu anda en yüksek stoğa sahip ürün **{top['name']}** görünüyor.",
        f"Toplam stok: {top['stock']} adet.",
    ]
    if sales <= 5:
        parts.append("Stok riski görünmüyor, fakat satış hareketi düşük; bu üründe ivme yaratmak iyi olabilir.")
    else:
        parts.append(f"Aynı zamanda satışlar da {sales} adet — sağlıklı bir akış.")

    recs = _recs(["growth_marketing", "discount_promotion", "insights"]) if sales <= 5 \
        else _recs(["insights"])

    return {
        "intent": "top_stock_product",
        "answer": "\n\n".join(parts),
        "data": {
            "item": top,
            "metric": "stock",
        },
        "recommendations": recs,
        "confidence": 0.92,
    }


def low_stock_products(user_id: int = 1, threshold: int = 10, limit: int = 5) -> dict:
    sql = """
        SELECT i.id, i.name, i.stock, i.price, i.sales, s.name AS store_name
        FROM items i LEFT JOIN stores s ON s.id = i.store_id
        WHERE i.stock IS NOT NULL AND i.stock < ?
        ORDER BY i.stock ASC LIMIT ?
    """
    rows = _fake_query(sql, (threshold, limit))
    if not rows:
        return {
            "intent": "low_stock_products",
            "answer": (
                f"Şu anda stoğu {threshold} adedin altında olan bir ürün görmüyorum. "
                "Stok tarafında acil bir aksiyon gerekmiyor."
            ),
            "data": {"items": [], "threshold": threshold},
            "recommendations": [],
            "confidence": 0.9,
        }

    lines = [f"Düşük stoklu {len(rows)} ürün dikkat çekiyor:"]
    for r in rows:
        lines.append(f"  • **{r['name']}** — {r['stock']} adet")
    if any((r["sales"] or 0) > 5 for r in rows):
        lines.append("\nBunların bir kısmı son dönemde satış görmüş — yenileme öncelikli olabilir.")
    return {
        "intent": "low_stock_products",
        "answer": "\n".join(lines),
        "data": {"items": rows, "threshold": threshold},
        "recommendations": _recs(["inventory_review", "insights"]),
        "confidence": 0.9,
    }


# ---------------------------------------------------------------------------
# Sales retrievers
# ---------------------------------------------------------------------------


def top_selling_product(user_id: int = 1, store_id: int | None = None) -> dict:
    sql = """
        SELECT i.id, i.name, i.stock, i.price, i.sales, s.name AS store_name
        FROM items i LEFT JOIN stores s ON s.id = i.store_id
        WHERE (i.sales IS NOT NULL AND i.sales > 0)
    """
    params: list = []
    if store_id is not None:
        sql += " AND i.store_id = ?"
        params.append(store_id)
    sql += " ORDER BY i.sales DESC LIMIT 1"

    rows = _fake_query(sql, tuple(params))
    if not rows:
        return _empty("henüz satış kaydı görünmüyor")

    top = rows[0]
    parts = [
        f"Şu anda en çok satış alan ürün **{top['name']}** görünüyor.",
        f"Toplam satış: {top['sales']} adet.",
    ]
    if (top["stock"] or 0) < 10:
        parts.append("Ancak stok seviyesi oldukça düşük olduğu için kısa süre içinde tükenme riski oluşabilir.")
        recs = _recs(["inventory_review", "growth_marketing"])
    elif (top["stock"] or 0) > 50 and top["sales"] > 10:
        parts.append("Stok da yeterli, ürünün ivmesi sağlıklı görünüyor.")
        recs = _recs(["growth_marketing", "insights"])
    else:
        recs = _recs(["growth_marketing"])
    return {
        "intent": "top_selling_product",
        "answer": "\n\n".join(parts),
        "data": {"item": top, "metric": "sales"},
        "recommendations": recs,
        "confidence": 0.92,
    }


def top_n_selling(user_id: int = 1, limit: int = 5) -> dict:
    sql = """
        SELECT i.id, i.name, i.sales, i.stock, s.name AS store_name
        FROM items i LEFT JOIN stores s ON s.id = i.store_id
        WHERE i.sales > 0 ORDER BY i.sales DESC LIMIT ?
    """
    rows = _fake_query(sql, (limit,))
    if not rows:
        return _empty("henüz satış kaydı görünmüyor")
    lines = ["En çok satan ürünler şu anda:"]
    for r in rows:
        lines.append(f"  • **{r['name']}** — {r['sales']} satış (stok {r['stock']})")
    return {
        "intent": "top_n_selling",
        "answer": "\n".join(lines),
        "data": {"items": rows},
        "recommendations": _recs(["growth_marketing", "insights"]),
        "confidence": 0.9,
    }


def sales_drop_diagnosis(user_id: int = 1) -> dict:
    """Why are sales dropping? Correlate sales events + reviews + shipping."""
    from cross_event_reasoner import reason_across_events

    cross = reason_across_events(user_id)
    signals = cross.get("signal_counts", {}) or {}

    causes = []
    if signals.get("negative_reviews", 0) >= 0.6:
        causes.append("negatif yorum artışı")
    if signals.get("shipping_delay", 0) >= 0.6:
        causes.append("kargo gecikmesi sinyalleri")
    if signals.get("low_stock", 0) >= 0.6:
        causes.append("bazı ürünlerde stok azalması")
    if signals.get("engagement_spike", 0) < 0.2 and signals.get("campaign", 0) >= 0.4:
        causes.append("düşük banner / kampanya dönüşümü")

    sales_now = _fake_query(
        "SELECT COALESCE(SUM(sales),0) AS total FROM items"
    )
    total_sales = sales_now[0]["total"] if sales_now else 0

    if not causes:
        return {
            "intent": "sales_drop_diagnosis",
            "answer": (
                "Şu an satış düşüşünü destekleyen net bir çapraz sinyal göremiyorum. "
                f"Toplam satış {total_sales} adet seviyesinde. "
                "İstersen kısa bir trend analizi çalıştırabilirim."
            ),
            "data": {"total_sales": total_sales, "signals": signals},
            "recommendations": _recs(["insights"]),
            "confidence": 0.55,
        }

    answer_parts = [
        "Son birkaç gündeki verilere bakınca satış düşüşünün birkaç sebeple bağlantılı olabileceğini görüyorum.",
        "Özellikle:",
        "\n".join(f"  • {c}" for c in causes),
        "birlikte satış performansını etkiliyor olabilir.",
    ]
    return {
        "intent": "sales_drop_diagnosis",
        "answer": "\n\n".join(answer_parts),
        "data": {
            "total_sales": total_sales,
            "signals": signals,
            "causes": causes,
            "primary_hypothesis": cross.get("primary_hypothesis"),
        },
        "recommendations": _recs(["reputation", "discount_promotion", "insights"]),
        "confidence": min(0.9, 0.55 + 0.1 * len(causes)),
    }


def sales_overview(user_id: int = 1) -> dict:
    items = _fake_query(
        "SELECT name, sales, stock, price FROM items ORDER BY sales DESC"
    )
    if not items:
        return _empty("ürün katalogu boş")

    total_sales = sum((i["sales"] or 0) for i in items)
    total_stock = sum((i["stock"] or 0) for i in items)
    revenue = round(sum((i["sales"] or 0) * (i["price"] or 0) for i in items), 2)
    top = items[0]
    parts = [
        f"Toplam satış {total_sales} adet, tahmini ciro {revenue} ₺.",
        f"En çok satan ürün **{top['name']}** ({top['sales']} adet).",
    ]
    if total_sales < 50:
        parts.append("Hacim oldukça düşük — büyütme aksiyonu düşünebiliriz.")
        recs = _recs(["discount_promotion", "growth_marketing"])
    elif total_sales > 250:
        parts.append("Hacim sağlıklı — momentumu sürdürmek için Instagram paylaşımı etkili olur.")
        recs = _recs(["growth_marketing"])
    else:
        parts.append("Hacim istikrarlı görünüyor.")
        recs = _recs(["insights"])
    return {
        "intent": "sales_overview",
        "answer": "\n\n".join(parts),
        "data": {
            "total_sales": total_sales,
            "total_stock": total_stock,
            "estimated_revenue": revenue,
            "top": top,
        },
        "recommendations": recs,
        "confidence": 0.88,
    }


# ---------------------------------------------------------------------------
# Sentiment / reviews
# ---------------------------------------------------------------------------


def sentiment_status(user_id: int = 1) -> dict:
    rows = _fake_query(
        """
        SELECT sentiment, COUNT(*) AS c FROM reviews GROUP BY sentiment
        """
    )
    counts = {r["sentiment"]: r["c"] for r in rows}
    neg = counts.get("negative", 0)
    pos = counts.get("positive", 0)
    neu = counts.get("neutral", 0)
    total = neg + pos + neu

    if total == 0:
        return {
            "intent": "sentiment_status",
            "answer": "Henüz yorum verisi yok — duygu analizi için biraz daha veri gerekiyor.",
            "data": {"counts": counts},
            "recommendations": [],
            "confidence": 0.5,
        }

    neg_ratio = neg / total if total else 0
    if neg_ratio >= 0.4:
        tone = "olumsuz"
        parts = [
            f"Müşteri yorumları genelinde olumsuz bir eğilim göze çarpıyor ({neg} olumsuz / {total} yorum).",
            "Bu duruma sebep olabilecek operasyonel sorunları araştırmak iyi olur — özellikle kargo süreci ve son kampanyalar.",
        ]
        recs = _recs(["reputation", "shipping_response", "customer_support"])
    elif neg_ratio <= 0.15:
        tone = "olumlu"
        parts = [
            f"Yorum tarafı gayet olumlu görünüyor ({pos} olumlu / {total} yorum).",
            "Bu momentumu Instagram'da bir başarı paylaşımıyla değerlendirebiliriz.",
        ]
        recs = _recs(["growth_marketing"])
    else:
        tone = "dengeli"
        parts = [
            f"Yorum tarafı dengeli görünüyor: {pos} olumlu, {neu} nötr, {neg} olumsuz.",
            "Genel havayı iyileştirmek için olumsuz yorumlara hızlı destek cevabı işe yarayabilir.",
        ]
        recs = _recs(["customer_support"])

    return {
        "intent": "sentiment_status",
        "answer": "\n\n".join(parts),
        "data": {
            "counts": counts,
            "tone": tone,
            "negative_ratio": round(neg_ratio, 3),
        },
        "recommendations": recs,
        "confidence": 0.85,
    }


# ---------------------------------------------------------------------------
# Workflows + approvals
# ---------------------------------------------------------------------------


_HUMAN_INTENT_LABELS = {
    "discount_promotion":   "indirim kampanyası",
    "growth_marketing":     "büyüme kampanyası",
    "marketing_campaign":   "kampanya akışı",
    "reputation":           "itibar koruma akışı",
    "shipping_response":    "kargo bilgilendirme akışı",
    "customer_support":     "müşteri destek akışı",
    "inventory_review":     "stok analizi",
    "insights":             "trend analizi",
    "low_stock_alert":      "düşük stok uyarısı",
    "store_welcome":        "yeni mağaza karşılama",
}


def _humanize_workflow(name: str) -> str:
    """Turn 'discount_promotion_item_42_old_name' into 'İndirim kampanyası'."""
    if not name:
        return "AI iş akışı"
    for intent, label in _HUMAN_INTENT_LABELS.items():
        if name.startswith(intent):
            return label.capitalize()
    if "instagram" in name:
        return "Instagram paylaşımı"
    if "banner" in name:
        return "Banner kampanyası"
    if "campaign" in name:
        return "Kampanya akışı"
    if "stock" in name:
        return "Stok uyarısı"
    return "AI iş akışı"


def workflows_summary(user_id: int = 1, limit: int = 8) -> dict:
    rows = execute_query(
        """
        SELECT id, workflow_name, status, entity_type, entity_id, created_at
        FROM workflow_instances
        WHERE user_id=? ORDER BY id DESC LIMIT ?
        """,
        (user_id, limit),
    )
    if not rows:
        return {
            "intent": "workflows_summary",
            "answer": "Şu an aktif veya yakın geçmişte tetiklenmiş bir iş akışı görmüyorum.",
            "data": {"workflows": []},
            "recommendations": [],
            "confidence": 0.5,
        }

    active = [r for r in rows if r["status"] in ("scheduled", "running")]
    completed = [r for r in rows if r["status"] == "completed"]
    cancelled = [r for r in rows if r["status"] == "cancelled"]

    lines = []
    if active:
        lines.append(f"Şu anda {len(active)} aktif iş akışı çalışıyor.")
        for r in active[:3]:
            lines.append(f"  • {_humanize_workflow(r['workflow_name'])}")
    if completed:
        lines.append(f"Son dönemde {len(completed)} iş akışı tamamlandı.")
    if cancelled:
        lines.append(f"{len(cancelled)} iş akışı güvenlik veya geçerlilik kontrolüyle iptal edildi.")
    if not lines:
        lines = ["Son iş akışı durumunu özetliyorum."]

    return {
        "intent": "workflows_summary",
        "answer": "\n".join(lines),
        "data": {
            "workflows": [dict(r) for r in rows],
            "active_count": len(active),
            "completed_count": len(completed),
            "cancelled_count": len(cancelled),
        },
        "recommendations": [],
        "confidence": 0.85,
    }


def approval_bottlenecks(user_id: int = 1, limit: int = 5) -> dict:
    rows = execute_query(
        """
        SELECT id, workflow_name, reason, risk_level, confidence, created_at
        FROM approval_requests
        WHERE user_id=? AND status='pending'
        ORDER BY id DESC LIMIT ?
        """,
        (user_id, limit),
    )
    if not rows:
        return {
            "intent": "approval_bottlenecks",
            "answer": "Şu anda bekleyen bir AI önerisi yok — onay kuyruğu temiz.",
            "data": {"pending": []},
            "recommendations": [],
            "confidence": 0.9,
        }
    lines = [f"{len(rows)} AI önerisi onayını bekliyor:"]
    for r in rows:
        wf = _humanize_workflow(r["workflow_name"])
        lines.append(f"  • {wf} (risk: {r['risk_level']}, güven: {r['confidence']:.0%})")
    return {
        "intent": "approval_bottlenecks",
        "answer": "\n".join(lines),
        "data": {"pending": [dict(r) for r in rows]},
        "recommendations": [],
        "confidence": 0.9,
    }


# ---------------------------------------------------------------------------
# Campaigns / shipping / memory
# ---------------------------------------------------------------------------


def campaigns_performance(user_id: int = 1, limit: int = 5) -> dict:
    rows = execute_query(
        """
        SELECT workflow_name, business_intent, outcome, measured_at
        FROM planner_outcomes
        WHERE user_id=? AND business_intent IN ('discount_promotion','growth_marketing','marketing_campaign')
        ORDER BY id DESC LIMIT ?
        """,
        (user_id, limit),
    )
    if not rows:
        return {
            "intent": "campaigns_performance",
            "answer": "Yakın geçmişte ölçülmüş bir kampanya sonucu yok.",
            "data": {"campaigns": []},
            "recommendations": _recs(["growth_marketing"]),
            "confidence": 0.5,
        }

    success = [r for r in rows if r["outcome"] in ("approved", "auto_applied", "completed_success")]
    failed = [r for r in rows if r["outcome"] in ("rejected", "completed_failed")]

    parts = []
    if success:
        parts.append(f"Son {len(success)} kampanya başarıyla devreye alındı veya onaylandı.")
    if failed:
        parts.append(f"{len(failed)} kampanya kullanıcı tarafından reddedildi veya başarısız sonuçlandı.")
    if not parts:
        parts.append("Kampanya akışı ölçülüyor ama henüz kesin bir sonuç çıkmadı.")

    return {
        "intent": "campaigns_performance",
        "answer": "\n".join(parts),
        "data": {"campaigns": [dict(r) for r in rows]},
        "recommendations": _recs(["growth_marketing", "insights"]),
        "confidence": 0.8,
    }


def shipping_health(user_id: int = 1) -> dict:
    delayed = _fake_query(
        "SELECT COUNT(*) AS c FROM orders WHERE status='delayed'"
    )
    total = _fake_query("SELECT COUNT(*) AS c FROM orders")
    d = delayed[0]["c"] if delayed else 0
    t = total[0]["c"] if total else 0

    if t == 0:
        return _empty("henüz sipariş yok")

    ratio = d / t
    if ratio >= 0.2:
        parts = [
            f"Siparişlerin {d}/{t} tanesinde kargo gecikmesi var — bu %{ratio*100:.0f} oranında bir sıkıntı demek.",
            "Müşteriye proaktif bir bilgilendirme yollamak şikayetlerin önüne geçer.",
        ]
        recs = _recs(["shipping_response", "customer_support"])
    elif d > 0:
        parts = [
            f"Sadece {d}/{t} siparişte gecikme var — risk düşük ama izlemeye değer.",
        ]
        recs = _recs(["shipping_response"])
    else:
        parts = [f"Kargo tarafı temiz görünüyor — {t} siparişin tamamı zamanında ilerliyor."]
        recs = []
    return {
        "intent": "shipping_health",
        "answer": "\n\n".join(parts),
        "data": {"delayed": d, "total": t, "delay_ratio": round(ratio, 3)},
        "recommendations": recs,
        "confidence": 0.88,
    }


def memory_patterns(user_id: int = 1, limit: int = 5) -> dict:
    """What has the AI learned? Outcome aggregates by intent."""
    rows = execute_query(
        """
        SELECT business_intent, outcome, COUNT(*) AS c
        FROM planner_outcomes
        WHERE user_id=? AND business_intent IS NOT NULL
        GROUP BY business_intent, outcome
        ORDER BY c DESC LIMIT 30
        """,
        (user_id,),
    )
    if not rows:
        return {
            "intent": "memory_patterns",
            "answer": "Henüz yeterli kararı ölçemedim. Birkaç onay/reddetme sonrası örüntüleri görmeye başlarım.",
            "data": {"patterns": []},
            "recommendations": [],
            "confidence": 0.4,
        }

    by_intent: dict[str, dict[str, int]] = {}
    for r in rows:
        key = r["business_intent"]
        by_intent.setdefault(key, {"total": 0, "success": 0, "failure": 0})
        by_intent[key]["total"] += r["c"]
        if r["outcome"] in ("approved", "auto_applied", "completed_success"):
            by_intent[key]["success"] += r["c"]
        elif r["outcome"] in ("rejected", "completed_failed"):
            by_intent[key]["failure"] += r["c"]

    lines = ["AI'ın son dönemde öğrendiği örüntüler:"]
    patterns = []
    for intent, stats in sorted(by_intent.items(), key=lambda kv: -kv[1]["total"])[:limit]:
        label = _HUMAN_INTENT_LABELS.get(intent, intent)
        succ = stats["success"]
        fail = stats["failure"]
        if succ > fail and succ >= 2:
            lines.append(f"  • {label} → genellikle olumlu sonuçlanıyor ({succ} başarılı)")
            patterns.append({"intent": intent, "trend": "positive", **stats})
        elif fail > succ and fail >= 2:
            lines.append(f"  • {label} → son zamanlarda reddediliyor ({fail} ret)")
            patterns.append({"intent": intent, "trend": "negative", **stats})
        else:
            lines.append(f"  • {label} → veriler hâlâ toplanıyor ({stats['total']} kayıt)")
            patterns.append({"intent": intent, "trend": "neutral", **stats})

    return {
        "intent": "memory_patterns",
        "answer": "\n".join(lines),
        "data": {"patterns": patterns},
        "recommendations": [],
        "confidence": 0.8,
    }


# ---------------------------------------------------------------------------
# Operational pressure (operator quick-look)
# ---------------------------------------------------------------------------


def operational_pressure(user_id: int = 1) -> dict:
    sales = sales_overview(user_id)
    sentiment = sentiment_status(user_id)
    shipping = shipping_health(user_id)
    approvals = approval_bottlenecks(user_id)
    wfs = workflows_summary(user_id)

    pressure_score = 0
    notes: list[str] = []

    if sentiment["data"].get("negative_ratio", 0) >= 0.4:
        pressure_score += 2
        notes.append("müşteri duyarlılığı düşük")
    if shipping["data"].get("delay_ratio", 0) >= 0.2:
        pressure_score += 2
        notes.append("kargo gecikmesi yoğun")
    if approvals["data"].get("pending"):
        pressure_score += 1
        notes.append(f"{len(approvals['data']['pending'])} bekleyen onay")
    if wfs["data"].get("cancelled_count", 0) > 2:
        pressure_score += 1
        notes.append("iptal edilen iş akışları")

    if pressure_score >= 4:
        level = "yüksek"
    elif pressure_score >= 2:
        level = "orta"
    else:
        level = "düşük"

    parts = [
        f"Operasyonel baskı seviyesi şu an **{level}** görünüyor.",
    ]
    if notes:
        parts.append("Öne çıkan sebepler: " + ", ".join(notes) + ".")
    parts.append("Detaylar:\n" + sales["answer"])

    return {
        "intent": "operational_pressure",
        "answer": "\n\n".join(parts),
        "data": {
            "pressure_level": level,
            "score": pressure_score,
            "notes": notes,
            "sales": sales["data"],
            "sentiment": sentiment["data"],
            "shipping": shipping["data"],
            "approvals": approvals["data"],
            "workflows": wfs["data"],
        },
        "recommendations": (
            _recs(["reputation", "shipping_response", "insights"]) if pressure_score >= 2 else []
        ),
        "confidence": 0.85,
    }


# ---------------------------------------------------------------------------
# Humanized timeline (for the dashboard, not the chat)
# ---------------------------------------------------------------------------


def humanize_event(event: dict) -> dict | None:
    """Turn a raw timeline row into an operator narrative line.

    Returns None for events that should not appear in the operator timeline
    (e.g. synthetic tool emits if the dashboard wants them filtered).
    """
    group = event.get("group", "")
    name = event.get("event", "")
    payload = event.get("payload") or {}
    meta = event.get("meta") or {}
    subject = event.get("subject") or {}

    is_synthetic = (meta.get("source") == "tool_emit") or (
        meta.get("orchestration", {}).get("path") == "tool_execution"
    )

    label = None
    icon = "•"
    tone = "neutral"

    if group == "product" and name == "created":
        label = f"Yeni ürün eklendi: {payload.get('name', 'Ürün')}"
        icon = "🆕"; tone = "positive"
    elif group == "product" and name == "updated":
        if "discount" in payload:
            label = f"İndirim uygulandı: %{payload.get('discount')} — {payload.get('name', 'Ürün')}"
            icon = "💸"; tone = "positive"
        else:
            label = f"Ürün güncellendi: #{subject.get('id')}"
            icon = "✏️"
    elif group == "order" and name == "created":
        label = f"Yeni sipariş: {payload.get('quantity', 1)} adet"
        icon = "📦"; tone = "positive"
    elif group == "stock" and name == "updated":
        new_stock = payload.get("new_stock")
        if new_stock is not None and new_stock < 10:
            label = f"Düşük stok riski tespit edildi (kalan {new_stock} adet)"
            icon = "⚠️"; tone = "warning"
        else:
            label = f"Stok güncellendi: kalan {new_stock} adet"
            icon = "📊"
    elif group == "review":
        if name == "negative" or (payload.get("rating", 5) or 5) <= 2:
            label = f"Olumsuz müşteri yorumu (rating {payload.get('rating')})"
            icon = "🛑"; tone = "negative"
        else:
            label = f"Yeni müşteri yorumu (rating {payload.get('rating')})"
            icon = "🗨️"; tone = "positive"
    elif group == "customer":
        label = f"Müşteri sorusu: {(payload.get('question') or '')[:60]}"
        icon = "❓"
    elif group == "shipping":
        label = f"Kargo gecikmesi bildirildi"
        icon = "🚚"; tone = "negative"
    elif group == "campaign":
        label = f"Kampanya başlatıldı: {payload.get('name', 'Kampanya')}"
        icon = "🚀"; tone = "positive"
    elif group == "banner":
        ctr = payload.get("ctr")
        if ctr is not None and ctr > 0.08:
            label = f"Banner etkileşim artışı (CTR %{ctr*100:.1f})"
            icon = "📈"; tone = "positive"
        else:
            label = "Banner performansı güncellendi"
            icon = "🖼️"
    elif group == "store":
        if name == "created":
            label = f"Yeni mağaza açıldı: {payload.get('name', 'Mağaza')}"
            icon = "🏪"; tone = "positive"
        elif name == "rejected":
            label = "Mağaza reddedildi"
            icon = "🚫"; tone = "negative"
    elif group == "automation" or is_synthetic:
        tool = meta.get("tool_name") or "AI aracı"
        action_label = {
            "instagram_campaign_tool": "Instagram paylaşımı hazırlandı",
            "banner_generator_tool": "Banner taslağı hazırlandı",
            "coupon_generator_tool": "Kupon oluşturuldu",
            "faq_update_tool": "SSS güncellendi",
            "support_response_tool": "Destek cevabı hazırlandı",
            "trend_analysis_tool": "Trend analizi tamamlandı",
            "low_stock_notification_tool": "Düşük stok uyarısı gönderildi",
        }.get(tool, f"AI aracı çalıştı: {tool}")
        label = action_label
        icon = "🤖"; tone = "ai"

    if not label:
        return None  # raw technical events stay hidden from operator timeline

    return {
        "id": event.get("id"),
        "ts": event.get("ts"),
        "icon": icon,
        "tone": tone,
        "label": label,
        "group": group,
        "synthetic": is_synthetic,
    }


def humanized_timeline(user_id: int = 1, limit: int = 30) -> dict:
    from timeline_service import fetch_timeline

    raw = fetch_timeline(cursor=0, direction="desc", limit=limit * 2, user_id=user_id)
    humanized = []
    for ev in raw.get("data", []):
        h = humanize_event(ev)
        if h is not None:
            humanized.append(h)
        if len(humanized) >= limit:
            break
    return {"data": humanized, "count": len(humanized)}


# ---------------------------------------------------------------------------
# Insight cards for the dashboard
# ---------------------------------------------------------------------------


def insight_cards(user_id: int = 1) -> list[dict]:
    """Operator-facing insight cards. Each card carries severity, recommendations."""
    from business_intelligence import analyze
    from cross_event_reasoner import reason_across_events

    synthetic = {
        "id": 0, "group": "dashboard", "event": "snapshot",
        "description": "dashboard snapshot", "payload": {}, "changes": {},
    }
    bi = analyze(synthetic, "dashboard.snapshot", {}, user_id)
    cross = reason_across_events(user_id)

    cards: list[dict] = []

    for ins in (bi.get("insights") or [])[:6]:
        sev = "high" if ins.get("critical") else ("medium" if ins.get("strength", 0) >= 0.7 else "low")
        intent = {
            "sales_drop": "insights",
            "customer_dissatisfaction": "reputation",
            "reputation_risk": "reputation",
            "shipping_delay": "shipping_response",
            "inventory_risk": "inventory_review",
            "campaign_opportunity": "discount_promotion",
            "viral_product": "growth_marketing",
            "engagement_spike": "growth_marketing",
            "seasonal_opportunity": "marketing_campaign",
            "price_drop_promotion": "discount_promotion",
            "repeat_failures": "insights",
        }.get(ins["type"], "insights")
        cards.append({
            "kind": "bi",
            "type": ins["type"],
            "severity": sev,
            "title": ins.get("message", ins["type"]),
            "confidence": ins.get("strength", 0.5),
            "impact": _impact_for(ins["type"]),
            "recommended_intent": intent,
            "recommended_action": _INTENT_SUGGESTIONS.get(intent, intent),
            "data": ins.get("data", {}),
        })

    for h in (cross.get("hypotheses") or [])[:3]:
        sev = "high" if h.get("state_transition") == "deteriorating" else (
            "medium" if h.get("state_transition") == "concerning" else "low"
        )
        cards.append({
            "kind": "cross_event",
            "type": h["type"],
            "severity": sev,
            "title": h.get("message", h["type"]),
            "confidence": h.get("strength", 0.5),
            "impact": _impact_for(h["type"]),
            "evidence": h.get("evidence", []),
            "state_transition": h.get("state_transition", "stable"),
        })

    return cards


_IMPACT_DESCRIPTIONS = {
    "sales_drop": "satış hacmi",
    "customer_dissatisfaction": "müşteri sadakati",
    "reputation_risk": "marka itibarı",
    "shipping_delay": "teslimat deneyimi",
    "inventory_risk": "satılabilir stok",
    "campaign_opportunity": "büyüme fırsatı",
    "viral_product": "ürün momentum",
    "engagement_spike": "marka görünürlüğü",
    "seasonal_opportunity": "sezonsal gelir",
    "delivery_experience_issue": "teslimat deneyimi",
    "campaign_momentum": "büyüme momentum",
    "supply_chain_pressure": "tedarik sürekliliği",
    "stable_operations": "operasyonel istikrar",
    "growth_window": "büyüme penceresi",
    "reputation_strength": "marka itibarı",
    "promotion_working": "kampanya etkinliği",
}


def _impact_for(insight_type: str) -> str:
    return _IMPACT_DESCRIPTIONS.get(insight_type, "operasyonel performans")
