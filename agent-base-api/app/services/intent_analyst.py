"""Intent Analyst — gpt-4o-mini ile sorunun ne istediğini JSON olarak çıkarır.

max_tokens=200, sadece JSON döner. Türkçe argo + yanlış yazım dahil.
Hata durumunda FALLBACK_INTENT döner — sistem hiç durmaz.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


_SYSTEM_PROMPT = """Sen bir e-ticaret veri analisti asistanısın.
Kullanıcının sorusunu analiz et. Türkçe argo, yanlış yazım, eksik harf
olsa bile doğru anla. "Malım var mı" = stok sorgusu, "Cebime ne giriyor" = kar sorgusu.

Mevcut tablolar:
- products: name, price, cost_price, discount, stock_quantity, stock_alert_level,
  rating, rating_count, category, brand, sku, is_active, status
- stores: name, rating, status
- product_reviews: rating, content, review_date
- product_price_history: old_price, new_price, change_reason, changed_at
- orders: status, total_amount, ordered_at, customer_name
- order_items: quantity, unit_price, line_total
- stock_movements: movement_type, quantity, stock_after, moved_at
- product_daily_metrics: views, clicks, add_to_cart, purchases, revenue, date
- store_daily_metrics: total_orders, total_revenue, total_visitors, new_customers, date
- campaign_performance: campaign_name, total_orders, total_revenue, roi, start_date
- customers: name, total_orders, total_spent, last_order_at, tags

Intent seçim kuralları — MUTLAKA UY:
- stock_check: stok miktarı soruları. "Malım var mı", "kaç adet kaldı", "tükendi mi"
- profit_analysis: kar, marj, maliyet soruları. "Cebime ne giriyor", "kar marjım"
- review_analysis: müşteri yorumları, rating soruları. "Yorumlar nasıl", "müşteriler ne diyor"
- sales_analysis: sipariş, satış, ciro soruları. "Kaç sattım", "bu ay gelir"
- price_analysis: fiyat, indirim soruları. "Fiyatlar nasıl", "indirim yapmalı mıyım"
- price_history: fiyat geçmişi. "Fiyat değişti mi", "eskiden ne kadardı"
- campaign_analysis: kampanya performansı. "Kampanya nasıl gitti", "ROI ne"
- customer_analysis: müşteri listesi. "Müşterilerim kimler", "VIP müşteri"
- store_info: SADECE mağaza adı veya mağaza rating'i istendiğinde.
  "Mağazalarımın isimleri neler", "mağaza puanım kaç" — başka hiçbir şey sorulmuyorsa.
- general_overview: mağaza + ürün + stok + rating BİRLİKTE istendiğinde VEYA genel özet istendiğinde.
  "Kaç mağazam var ve ürün sayıları", "genel durum nasıl", "özet ver", "neler var",
  "kaç ürünüm var", "sistemde ne var", "mağazalarda kaç ürün var".
  Birden fazla varlık türü (mağaza VE ürün) soruluyorsa MUTLAKA general_overview seç.

model_tier kuralları:
- "mini": tek boyutlu analiz, yorum özeti, basit karşılaştırma
- "full": çok boyutlu stratejik analiz, platform geneli admin sorgusu

SADECE JSON döndür, başka hiçbir şey yazma:
{
  "intent": "stock_check|profit_analysis|review_analysis|sales_analysis|price_analysis|campaign_analysis|customer_analysis|general_overview|store_info|price_history",
  "tables_needed": ["products"],
  "columns_needed": ["name", "stock_quantity"],
  "filters": {"product_name": null, "date_range_days": null},
  "aggregation": false,
  "model_tier": "mini",
  "confidence": 0.95,
  "reasoning": "Stok sorgusu"
}"""


_ALLOWED_INTENTS = {
    "stock_check", "profit_analysis", "review_analysis", "sales_analysis",
    "price_analysis", "campaign_analysis", "customer_analysis",
    "general_overview", "store_info", "price_history",
}


@dataclass
class IntentAnalysis:
    intent: str
    tables_needed: list[str] = field(default_factory=list)
    columns_needed: list[str] = field(default_factory=list)
    filters: dict = field(default_factory=dict)
    aggregation: bool = False
    model_tier: str = "mini"
    confidence: float = 0.0
    reasoning: str = ""


FALLBACK_INTENT = IntentAnalysis(
    intent="general_overview",
    tables_needed=["products", "stores"],
    columns_needed=["name", "price", "stock_quantity", "rating"],
    filters={},
    aggregation=False,
    model_tier="mini",
    confidence=0.0,
    reasoning="fallback",
)


def _coerce(raw: dict) -> IntentAnalysis:
    intent = (raw.get("intent") or "general_overview").strip().lower()
    if intent not in _ALLOWED_INTENTS:
        intent = "general_overview"
    tier = (raw.get("model_tier") or "mini").lower()
    if tier not in ("mini", "full"):
        tier = "mini"
    return IntentAnalysis(
        intent=intent,
        tables_needed=list(raw.get("tables_needed") or []),
        columns_needed=list(raw.get("columns_needed") or []),
        filters=dict(raw.get("filters") or {}),
        aggregation=bool(raw.get("aggregation", False)),
        model_tier=tier,
        confidence=float(raw.get("confidence", 0.5)),
        reasoning=(raw.get("reasoning") or "")[:200],
    )


def analyze_intent(
    question: str,
    user_id: int,
    *,
    api_key: str | None = None,
) -> IntentAnalysis:
    """Sorudan IntentAnalysis çıkar. Hata olursa FALLBACK_INTENT döner."""
    q = (question or "").strip()
    if not q:
        return FALLBACK_INTENT

    key = api_key or os.environ.get("OPENAI_API_KEY")
    if not key:
        return FALLBACK_INTENT

    try:
        from openai import OpenAI

        client = OpenAI(api_key=key, timeout=8)
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": q},
            ],
            temperature=0,
            max_tokens=200,
            response_format={"type": "json_object"},
        )
        raw_text = (completion.choices[0].message.content or "").strip()
        if not raw_text:
            return FALLBACK_INTENT
        return _coerce(json.loads(raw_text))
    except Exception as exc:
        print(f"[INTENT_ANALYST] failed: {exc}")
        return FALLBACK_INTENT