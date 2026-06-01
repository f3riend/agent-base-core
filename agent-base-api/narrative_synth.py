"""
Narrative synthesis — turns structured business signals into operator-grade prose.

Goal: the business_chat output should read like an ops strategist talking to
a store owner, not a debug dump. Pure deterministic pipeline by default so the
runtime stays explainable and replayable. An optional CrewAI restyle pass
sits on top and is only invoked when explicitly enabled.

Pipeline:
    1. signal extraction      → typed signals from state + BI + cross + memory
    2. correlation            → handwritten causal rules
    3. narrative templates    → Turkish operator voice
    4. action recommendations → only intents that exist in ontology
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from ontology import INTENTS, default_tools_for_intent, is_known_intent


# ---------------------------------------------------------------------------
# Signal extraction
# ---------------------------------------------------------------------------


@dataclass
class Signal:
    name: str
    direction: str            # "up" | "down" | "flat" | "negative" | "positive"
    strength: float           # 0..1
    summary: str
    data: dict = field(default_factory=dict)


def _signals_from_state(state: dict) -> list[Signal]:
    out: list[Signal] = []
    sales = state.get("sales") or {}
    inventory = state.get("inventory") or {}
    engagement = state.get("engagement") or {}
    sentiment = state.get("sentiment") or "neutral"

    total_sales = sales.get("total_item_sales") or 0
    if total_sales > 0:
        if total_sales < 50:
            out.append(Signal("sales", "down", 0.7,
                              f"Toplam satış {total_sales} adet — düşük seyrediyor"))
        elif total_sales > 250:
            out.append(Signal("sales", "up", 0.65,
                              f"Toplam satış {total_sales} adet — sağlıklı"))
        else:
            out.append(Signal("sales", "flat", 0.45,
                              f"Toplam satış {total_sales} adet — istikrarlı"))

    low = inventory.get("low_stock_count", 0)
    health = inventory.get("health", "healthy")
    if low > 0:
        out.append(Signal(
            "inventory", "negative" if health == "critical" else "down",
            0.9 if health == "critical" else 0.55,
            f"{low} ürün düşük stokta (sağlık={health})",
            {"low_stock_items": inventory.get("low_stock_items", [])},
        ))

    neg = engagement.get("negative_reviews", 0)
    if neg >= 3:
        out.append(Signal(
            "sentiment", "negative", min(1.0, 0.5 + neg * 0.1),
            f"{neg} olumsuz yorum kayıtlı — memnuniyet sinyali zayıf",
            {"count": neg},
        ))
    elif sentiment == "positive":
        out.append(Signal("sentiment", "positive", 0.55,
                          "Müşteri duyarlılığı genel olarak olumlu"))

    pending = engagement.get("pending_approvals", 0)
    if pending > 0:
        out.append(Signal(
            "approvals", "flat", 0.4,
            f"{pending} bekleyen onay var",
            {"count": pending},
        ))

    return out


def _signals_from_bi(bi: dict) -> list[Signal]:
    out: list[Signal] = []
    for ins in (bi or {}).get("insights", [])[:6]:
        out.append(Signal(
            name=f"bi:{ins.get('type', 'insight')}",
            direction="negative" if ins.get("critical") else "flat",
            strength=float(ins.get("strength", 0.5)),
            summary=ins.get("message", str(ins)),
            data=ins.get("data") or {},
        ))
    return out


def _signals_from_cross(cross: dict) -> list[Signal]:
    out: list[Signal] = []
    hyps = (cross or {}).get("hypotheses") or []
    for h in hyps[:3]:
        out.append(Signal(
            name=f"cross:{h.get('type', 'pattern')}",
            direction="negative" if "dissatisfaction" in h.get("type", "")
                       or "issue" in h.get("type", "")
                       or "pressure" in h.get("type", "") else "flat",
            strength=float(h.get("strength", 0.5)),
            summary=h.get("message", ""),
        ))
    return out


# ---------------------------------------------------------------------------
# Correlation — handwritten causal rules
# ---------------------------------------------------------------------------


def _correlate(signals: list[Signal]) -> list[str]:
    """Produce one-line correlation observations linking pairs of signals."""
    by_name = {s.name: s for s in signals}
    obs: list[str] = []

    if "sales" in by_name and by_name["sales"].direction == "down" and "sentiment" in by_name and by_name["sentiment"].direction == "negative":
        obs.append(
            "Satışlardaki düşüş ve olumsuz yorumlar birlikte gidiyor — "
            "müşteri memnuniyetiyle bağlantılı bir durum gibi görünüyor."
        )

    if any(s.name == "cross:delivery_experience_issue" for s in signals):
        obs.append(
            "Son olaylarda kargo süreciyle ilgili şikayet sinyali öne çıkıyor."
        )

    if any(s.name == "cross:campaign_momentum" for s in signals):
        obs.append(
            "Aktif kampanyalar etkileşim alıyor — büyütme penceresi açık."
        )

    if "inventory" in by_name and by_name["inventory"].direction == "negative":
        obs.append(
            "Stok tarafında kritik bir baskı var; satışı bozmadan önce yenileme önerilir."
        )

    if any(s.name == "cross:promotion_working" for s in signals):
        obs.append("İndirim aktivitesi etkili görünüyor; momentumu sürdürmek mümkün.")

    return obs


# ---------------------------------------------------------------------------
# Narrative + recommendations
# ---------------------------------------------------------------------------


# Intent → operator-facing soft suggestion phrase (Turkish).
_INTENT_SUGGESTIONS: dict[str, str] = {
    "discount_promotion":   "küçük bir indirim kampanyası",
    "growth_marketing":     "büyütme odaklı bir sosyal paylaşım",
    "marketing_campaign":   "bir kampanya akışı",
    "reputation":           "açıklayıcı bir paylaşım ve destek akışı",
    "shipping_response":    "kargo süreci için bir bilgilendirme",
    "customer_support":     "bekleyen müşteri sorularına bir cevap akışı",
    "inventory_review":     "düşük stoklu ürünler için bir hatırlatma",
    "insights":             "kısa bir trend analizi",
}


def _recommend(intents: list[str]) -> list[dict]:
    """Filter to known intents only; attach default tools."""
    recs = []
    for intent in intents:
        if not is_known_intent(intent):
            continue
        suggestion = _INTENT_SUGGESTIONS.get(intent)
        if not suggestion:
            continue
        recs.append({
            "intent": intent,
            "suggestion": suggestion,
            "default_tools": default_tools_for_intent(intent),
            "domain": INTENTS[intent].domain,
        })
    # De-duplicate by intent, keep first occurrence.
    seen, ordered = set(), []
    for r in recs:
        if r["intent"] in seen:
            continue
        seen.add(r["intent"])
        ordered.append(r)
    return ordered[:4]


def _infer_intents_from_signals(signals: list[Signal]) -> list[str]:
    intents: list[str] = []
    by_name = {s.name: s for s in signals}

    if by_name.get("sentiment") and by_name["sentiment"].direction == "negative":
        intents.append("reputation")
    if any(s.name == "cross:delivery_experience_issue" for s in signals):
        intents.append("shipping_response")
    if by_name.get("sales") and by_name["sales"].direction == "down":
        intents.extend(["discount_promotion", "insights"])
    if by_name.get("inventory") and by_name["inventory"].direction == "negative":
        intents.append("inventory_review")
    if any(s.name == "cross:campaign_momentum" for s in signals):
        intents.append("growth_marketing")
    if any(s.name.startswith("bi:viral_product") for s in signals):
        intents.append("growth_marketing")
    if any(s.name.startswith("bi:engagement_spike") for s in signals):
        intents.append("growth_marketing")
    return intents


def _render_opening(intent_hint: str, signals: list[Signal]) -> str:
    """First sentence — observation, not greeting."""
    if intent_hint == "sales":
        sales = next((s for s in signals if s.name == "sales"), None)
        if sales and sales.direction == "down":
            return "Son birkaç gündür satışlarda belirgin bir düşüş var gibi görünüyor."
        if sales and sales.direction == "up":
            return "Satış tarafı son dönemde olumlu ivme yakalamış görünüyor."
        return "Satış akışı şu an istikrarlı seyrediyor."

    if intent_hint == "products":
        return "Ürün performansına bakınca öne çıkan birkaç başlık var."

    if intent_hint == "campaigns":
        return "Kampanya tarafındaki son durumu özetliyorum."

    if intent_hint == "reviews":
        sentiment = next((s for s in signals if s.name == "sentiment"), None)
        if sentiment and sentiment.direction == "negative":
            return "Müşteri yorumlarında olumsuz bir eğilim göze çarpıyor."
        return "Son yorumlardaki genel hava şu an dengeli görünüyor."

    if intent_hint == "inventory":
        inv = next((s for s in signals if s.name == "inventory"), None)
        if inv and inv.direction == "negative":
            return "Stok tarafında dikkat etmemiz gereken bir nokta var."
        return "Stok durumu şu anda sağlıklı görünüyor."

    if intent_hint == "recommendations":
        return "Mevcut sinyallere göre öne çıkan birkaç öneri var."

    return "Mevcut iş durumunun genel resmini sana özetliyorum."


def _render_recommendation_block(recs: list[dict]) -> str:
    if not recs:
        return ""
    lines = ["", "İstersen şunları hazırlayabilirim:"]
    for r in recs:
        lines.append(f"  • {r['suggestion']}")
    return "\n".join(lines)


def synthesize_narrative(
    intent: str,
    state: dict,
    bi: dict,
    cross: dict,
    memory: dict | None = None,
    workflows: list[dict] | None = None,
) -> dict[str, Any]:
    """Compose a narrative answer from typed signals.

    Returns:
        {
            narrative: str,           # operator-tone Turkish prose
            recommendations: list,    # ontology-filtered action suggestions
            signals: list,            # the typed signals used (for trace)
            correlations: list,       # cross-signal observations
            confidence: float,
        }
    """
    signals: list[Signal] = []
    signals.extend(_signals_from_state(state or {}))
    signals.extend(_signals_from_bi(bi or {}))
    signals.extend(_signals_from_cross(cross or {}))

    correlations = _correlate(signals)
    intents = _infer_intents_from_signals(signals)
    recs = _recommend(intents)

    pieces: list[str] = [_render_opening(intent, signals)]
    if correlations:
        pieces.append("\n".join(correlations))
    elif signals:
        # Stronger signals as supporting prose
        top = sorted(signals, key=lambda s: -s.strength)[:2]
        for s in top:
            if s.strength >= 0.5 and s.summary:
                pieces.append(s.summary + ".")

    if workflows:
        last = workflows[0]
        wf_name = last.get("workflow_name")
        wf_status = last.get("status")
        if wf_name and wf_status:
            pieces.append(
                f"Son iş akışı `{wf_name}` ({wf_status}) — yakın takipte."
            )

    recommendation_block = _render_recommendation_block(recs)
    if recommendation_block:
        pieces.append(recommendation_block)

    narrative = "\n\n".join(p for p in pieces if p).strip()

    # Confidence: 0.4 floor; each strong signal adds 0.1; capped at 0.9.
    confidence = 0.4 + 0.1 * sum(1 for s in signals if s.strength >= 0.7)
    confidence = round(min(0.9, confidence), 2)

    out = {
        "narrative": narrative,
        "recommendations": recs,
        "signals": [
            {
                "name": s.name,
                "direction": s.direction,
                "strength": round(s.strength, 2),
                "summary": s.summary,
            }
            for s in signals
        ],
        "correlations": correlations,
        "confidence": confidence,
    }

    if os.environ.get("BUSINESS_CHAT_USE_AI", "0") == "1" and os.environ.get("OPENAI_API_KEY"):
        try:
            out["narrative"] = _restyle_with_ai(narrative, recs)
            out["ai_restyled"] = True
        except Exception as exc:
            print(f"[NARRATIVE] AI restyle failed, using deterministic: {exc}")
            out["ai_restyled"] = False

    return out


def _restyle_with_ai(narrative: str, recs: list[dict]) -> str:
    """Opsiyonel: LLM ile deterministic narrative'i daha doğal hale getir.

    Tur 2'de CrewAI bağımlılığı kaldırıldı; doğrudan OpenAI Chat
    Completions kullanıyoruz. Deterministic narrative source of truth —
    bu fonksiyon sadece tonu yumuşatır, olgular değişmemeli.
    """
    from openai import OpenAI

    rec_lines = "\n".join(f"- {r['suggestion']}" for r in recs)
    system_prompt = (
        "Sen bir Türkçe iş operasyon danışmanısın. Sana verilen olgusal "
        "özetin tonunu, bir mağaza operatörüne yaslı, doğal ve stratejik "
        "bir hâle getir. OLGULARI DEĞİŞTİRME. Net, kısa, öneri içeren "
        "bir Türkçe kullan. Markdown listeleri ve selamlama kullanma."
    )
    user_prompt = (
        "Aşağıdaki özet üzerinden 2-4 paragraflık doğal Türkçe bir yanıt yaz.\n\n"
        f"Özet:\n{narrative}\n\n"
        f"Önerilebilecek aksiyonlar:\n{rec_lines or 'yok'}\n\n"
        "Yanıt: doğrudan operatöre konuşurmuş gibi. Selamlama yok."
    )

    client = OpenAI(timeout=float(os.environ.get("BUSINESS_CHAT_RESTYLE_TIMEOUT", "12")))
    completion = client.chat.completions.create(
        model=os.environ.get("BUSINESS_CHAT_RESTYLE_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        temperature=0.6,
        max_tokens=600,
    )
    return (completion.choices[0].message.content or "").strip()
