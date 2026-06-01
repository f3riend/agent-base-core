"""
Business query router — natural language → retrieval intent.

The user-facing chat needs to answer real questions, not return generic
state summaries. This module classifies the question into one of the
retrieval intents handled by business_retrieval_service and dispatches.

If the question is broad/open-ended (no clear intent), `route()` returns
None and the caller (business_chat) falls back to narrative_synth.

Detection is keyword-based with a tiered scoring system — precise enough
for a Turkish/English mix without an LLM, and predictable enough for tests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

import business_retrieval_service as retriever


@dataclass
class Intent:
    name: str
    handler: Callable
    # Tokens that must appear (any of). Token = a substring match (case-insensitive).
    any_of: tuple[str, ...] = ()
    # If supplied, ALL groups must match — one token from each group.
    all_groups: tuple[tuple[str, ...], ...] = ()
    # Tokens that disqualify (negation / wrong topic).
    not_any_of: tuple[str, ...] = ()
    weight: float = 1.0


# Order matters: more specific intents declared first. The router walks the
# list, picks the highest-scoring match. Tied scores resolve to declaration
# order.

INTENTS_REGISTRY: list[Intent] = [
    # ---- stock-specific ----
    # Turkish allomorphs: "stok" → "stoğ" before vowel suffix (stoğu, stoğa,
    # stoğum, stoğunda). Substring prefix matcher will then catch all of
    # them via "stoğ" as well as "stok".
    Intent(
        name="top_stock_product",
        handler=lambda **kw: retriever.top_stock_product(**kw),
        all_groups=(
            ("stok", "stoğ", "stock", "depo", "envanter"),
            ("en çok", "en yüksek", "en fazla", "en bol", "en büyük",
             "highest", "most", "en"),
        ),
        not_any_of=("düşük", "kritik", "az kalan", "low",),
        weight=2.0,
    ),
    Intent(
        name="low_stock_products",
        handler=lambda **kw: retriever.low_stock_products(**kw),
        all_groups=(
            ("stok", "stoğ", "stock", "depo", "envanter"),
            ("düşük", "az", "kritik", "kalan", "azal", "tüken", "low"),
        ),
        weight=2.0,
    ),

    # ---- sales-specific ----
    # Turkish allomorphs: "satış"/"satıl"/"satıyor"/"satılan"/"satılıyor" all
    # share the stem "satı" — using "satı" prefix catches every form.
    Intent(
        name="top_selling_product",
        handler=lambda **kw: retriever.top_selling_product(**kw),
        all_groups=(
            ("satı", "satış", "sales", "selling"),
            ("en çok", "en yüksek", "en fazla", "en iyi", "top", "best", "most", "en"),
        ),
        not_any_of=("düşük", "düşüş", "azalış", "drop", "azaldı"),
        weight=2.0,
    ),
    Intent(
        name="top_n_selling",
        handler=lambda **kw: retriever.top_n_selling(**kw),
        any_of=(
            "en çok satan ürünler", "trend ürünler", "en iyi ürünler",
            "en çok satılan", "best sellers", "top selling products",
            "viral ürünler", "popüler ürünler",
        ),
        weight=2.0,
    ),
    Intent(
        name="sales_drop_diagnosis",
        handler=lambda **kw: retriever.sales_drop_diagnosis(**kw),
        all_groups=(
            ("satı", "satış", "sales", "gelir", "revenue", "ciro", "hasılat"),
            ("neden", "niye", "düş", "düşüş", "azalış", "azaldı", "kötü",
             "yavaşladı", "geriledi", "drop", "decline", "fall"),
        ),
        weight=2.5,
    ),
    Intent(
        name="sales_overview",
        handler=lambda **kw: retriever.sales_overview(**kw),
        any_of=(
            "satış durumu", "satış nasıl", "satış özet", "satış raporu",
            "satışlar nasıl", "ciro nasıl", "sales overview", "sales status",
        ),
        weight=1.5,
    ),

    # ---- sentiment / reviews ----
    # "yorum" → "yorumlar/yorumda/yorumla/yorumum" — "yorum" prefix covers all.
    Intent(
        name="sentiment_status",
        handler=lambda **kw: retriever.sentiment_status(**kw),
        any_of=(
            "yorum", "review", "memnun", "memnuniyet", "müşteri ne diyor",
            "şikayet", "olumsuz", "olumlu", "duyarlılık", "duygu",
            "sentiment",
        ),
        weight=1.5,
    ),

    # ---- workflows / approvals ----
    # "onay" → "onaylar/onayda/onayım/onaylanan" — "onay" prefix covers.
    Intent(
        name="approval_bottlenecks",
        handler=lambda **kw: retriever.approval_bottlenecks(**kw),
        any_of=(
            "onay", "bekleyen", "approval", "pending approval",
            "onay kuyru", "onayda", "onaylanacak",
        ),
        weight=1.8,
    ),
    Intent(
        name="workflows_summary",
        handler=lambda **kw: retriever.workflows_summary(**kw),
        any_of=(
            "iş akışı", "iş akışları", "workflow", "workflows", "akış",
            "ne yapıyor", "ne çalışıyor", "otomasyon", "otomasyonlar",
            "running",
        ),
        weight=1.5,
    ),

    # ---- campaigns / shipping ----
    # "kampanya" → "kampanyalar/kampanyada/kampanyanın" — "kampanya" prefix.
    Intent(
        name="campaigns_performance",
        handler=lambda **kw: retriever.campaigns_performance(**kw),
        all_groups=(
            ("kampanya", "campaign", "promosyon"),
            ("performans", "başarı", "sonuç", "ölç", "performance", "result"),
        ),
        weight=2.0,
    ),
    # "kargo" → "kargonun/kargoyla/kargoda" — "kargo" prefix covers all.
    Intent(
        name="shipping_health",
        handler=lambda **kw: retriever.shipping_health(**kw),
        any_of=(
            "kargo", "shipping", "teslimat", "delivery", "geç gelen",
            "kargo durumu", "shipping status", "gönderi",
        ),
        weight=1.6,
    ),

    # ---- memory / learning ----
    Intent(
        name="memory_patterns",
        handler=lambda **kw: retriever.memory_patterns(**kw),
        any_of=(
            "öğren", "hatırla", "geçmiş", "memory", "ne öğrendin",
            "pattern", "örüntü", "deneyim", "hafıza",
        ),
        weight=1.5,
    ),

    # ---- operational pressure ----
    Intent(
        name="operational_pressure",
        handler=lambda **kw: retriever.operational_pressure(**kw),
        any_of=(
            "operasyonel", "operasyon", "genel durum", "şirket durumu",
            "şu an ne durumda", "ne durumdayız", "operations",
            "iş nasıl gidiyor", "işletme nasıl", "company status",
            "baskı", "yoğunluk",
        ),
        weight=1.5,
    ),
]


_QUESTION_NORMALIZE_RE = re.compile(r"[^\w\sçğıöşüÇĞİÖŞÜ]+", flags=re.UNICODE)


def _normalize(text: str) -> str:
    text = (text or "").lower()
    text = _QUESTION_NORMALIZE_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _contains(text: str, token: str) -> bool:
    """Substring match for Turkish-style agglutinative morphology.

    Turkish suffixes attach directly to roots: "stokta" = "stok" + "da" (loc.),
    "yorumları" = "yorum" + "ları" (acc/pl.). A strict word boundary would
    miss these. We match the token as a prefix of any word in the text — that
    correctly handles "stok" in "stokta" while still rejecting unrelated
    substrings (e.g. "stop" would not match "stok" because there's no word
    starting with "stop" that has "stok" as its prefix).
    """
    token = token.lower()
    if " " in token:
        return token in text
    return re.search(rf"(^|\s){re.escape(token)}", text) is not None


def _score(intent: Intent, normalized: str) -> float:
    score = 0.0

    for tok in intent.not_any_of:
        if _contains(normalized, tok):
            return 0.0

    if intent.any_of:
        hits = sum(1 for tok in intent.any_of if _contains(normalized, tok))
        if hits == 0 and not intent.all_groups:
            return 0.0
        score += hits * 0.6

    if intent.all_groups:
        for group in intent.all_groups:
            if not any(_contains(normalized, tok) for tok in group):
                return 0.0
            score += 0.7
    return score * intent.weight


def detect_intent(question: str) -> Optional[Intent]:
    normalized = _normalize(question)
    if not normalized:
        return None
    best: tuple[float, Intent] | None = None
    for intent in INTENTS_REGISTRY:
        s = _score(intent, normalized)
        if s <= 0:
            continue
        if best is None or s > best[0]:
            best = (s, intent)
    if best is None or best[0] < 0.8:
        return None
    return best[1]


def route(question: str, *, user_id: int = 1, **extra) -> Optional[dict]:
    """Return a retriever response, or None for open-ended questions."""
    intent = detect_intent(question)
    if intent is None:
        return None
    try:
        result = intent.handler(user_id=user_id, **extra)
        if isinstance(result, dict):
            result.setdefault("routed_intent", intent.name)
            return result
        return None
    except TypeError:
        try:
            result = intent.handler(user_id=user_id)
            if isinstance(result, dict):
                result.setdefault("routed_intent", intent.name)
                return result
        except Exception as exc:
            print(f"[QUERY_ROUTER] handler error for {intent.name}: {exc}")
        return None
    except Exception as exc:
        print(f"[QUERY_ROUTER] handler error for {intent.name}: {exc}")
        return None


def list_supported_intents() -> list[str]:
    """For diagnostics — what the router can answer."""
    return [i.name for i in INTENTS_REGISTRY]
