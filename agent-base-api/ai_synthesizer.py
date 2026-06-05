"""
AI synthesizer — natural Turkish business-operator prose from real data.

This replaces the template-driven narrative path. The retrieval layer
(business_retrieval_service) still owns the **facts**. The synthesizer
turns those facts into a conversational answer that reads like an
operations strategist thinking out loud — varied phrasing, contextual
reasoning, references to the conversation so far.

Pipeline:

    1) compose_stages(retrieval, memory)
       — deterministic "thinking" lines derived from which retrievers fired
         and what they found. Always available; no LLM dependency.
         These power the dashboard's "deliberation" animation.

    2) compose_prompt(question, retrieval, memory, anti_phrases)
       — assembles a strict system prompt + user prompt. Encodes the
         operator persona, the data, the conversational history, and the
         anti-repetition list.

    3) synthesize_with_openai(...)
       — calls OpenAI Chat Completions if OPENAI_API_KEY is set and the
         feature flag CHAT_USE_LLM is not '0'. Streaming-safe; returns a
         full string. Falls back to the deterministic narrative on any
         failure path.

    4) synthesize(question, retrieval, memory)  ← public entry point
       — runs the pipeline. Returns {answer, stages, mode, latency_ms}.
         `mode` ∈ {"llm", "deterministic_fallback"} so callers know what
         shape the answer is in.

The synthesizer never re-queries the database. The retrieval layer is the
sole authority for facts; the synthesizer is the sole authority for tone
and variation.
"""

from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass
from typing import Any, Optional


CHAT_USE_LLM = os.environ.get("CHAT_USE_LLM", "1") != "0"
CHAT_LLM_MODEL = os.environ.get("CHAT_LLM_MODEL", "gpt-4o-mini")
CHAT_LLM_TIMEOUT = float(os.environ.get("CHAT_LLM_TIMEOUT_SEC", "20"))
CHAT_LLM_TEMPERATURE = float(os.environ.get("CHAT_LLM_TEMPERATURE", "0.6"))
CHAT_LLM_MAX_TOKENS = int(os.environ.get("CHAT_LLM_MAX_TOKENS", "500"))


try:
    from pg_context_formatter import format_pg_context, format_op_context
    _PG_FORMATTER_AVAILABLE = True
except ImportError:
    _PG_FORMATTER_AVAILABLE = False
    def format_pg_context(x): return ""
    def format_op_context(x): return ""

# ---------------------------------------------------------------------------
# Deliberation stages — deterministic, varied
# ---------------------------------------------------------------------------


# Pools per retrieval-intent. We randomise within a pool to keep the
# operator-side animation alive without sounding scripted. These are
# NOT canned responses — they're "what the AI is looking at right now"
# lines that appear during the brief retrieval window.

_STAGE_POOLS: dict[str, list[str]] = {
    "top_stock_product": [
        "Stok kayıtlarına bakıyorum…",
        "En yüksek stoğu olan ürünü çıkarıyorum…",
        "Stok dağılımını tarıyorum…",
    ],
    "low_stock_products": [
        "Düşük stoklu ürünleri tarıyorum…",
        "Stok eşiklerini gözden geçiriyorum…",
        "Stoğu eriyen kalemleri çıkarıyorum…",
    ],
    "top_selling_product": [
        "Son satış hareketlerine bakıyorum…",
        "Satış sıralamasını kontrol ediyorum…",
        "Hangi ürün öne çıkmış, ona bakıyorum…",
    ],
    "top_n_selling": [
        "Top satış listesini çıkarıyorum…",
        "Ürünleri satışa göre sıralıyorum…",
    ],
    "sales_drop_diagnosis": [
        "Satış düşüşünün izini sürüyorum…",
        "Yorum, kargo ve satış sinyallerini karşılaştırıyorum…",
        "Geçen birkaç güne ait olayları inceliyorum…",
    ],
    "sales_overview": [
        "Toplam satış tablosuna bakıyorum…",
        "Satış hacmini ve top ürünleri toparlıyorum…",
    ],
    "sentiment_status": [
        "Yorum trendlerini inceliyorum…",
        "Müşteri duyarlılığını ölçüyorum…",
        "Olumlu / olumsuz yorum oranlarını çıkarıyorum…",
    ],
    "workflows_summary": [
        "Aktif iş akışlarına bakıyorum…",
        "Hangi otomasyonlar çalışıyor, listeliyorum…",
    ],
    "approval_bottlenecks": [
        "Onay kuyruğunu kontrol ediyorum…",
        "Bekleyen AI önerilerini topluyorum…",
    ],
    "campaigns_performance": [
        "Kampanya sonuçlarını karşılaştırıyorum…",
        "Onaylanan ve reddedilen kampanyaları sayıyorum…",
    ],
    "shipping_health": [
        "Kargo / teslimat durumuna bakıyorum…",
        "Gecikme oranını hesaplıyorum…",
    ],
    "memory_patterns": [
        "Hafızadaki örüntülere bakıyorum…",
        "Hangi aksiyonlar genelde işe yarıyor, çıkarıyorum…",
    ],
    "operational_pressure": [
        "Genel operasyon baskısını ölçüyorum…",
        "Satış, kargo ve duyarlılığı bir araya getiriyorum…",
    ],
}

_GENERIC_OPENERS = [
    "Bu soruyu anlamaya çalışıyorum…",
    "Bağlamı topluyorum…",
    "İlgili verilere bakıyorum…",
]

_GENERIC_CLOSERS = [
    "Sonuçları derliyorum…",
    "Bulguları bir araya getiriyorum…",
    "Açıklamayı şekillendiriyorum…",
]


def _data_aware_first_stage(intent: str | None, data: dict | None) -> str | None:
    """If retrieval surfaced a concrete entity/metric, NAME it in the first stage.

    This is the single biggest perception change: instead of "Stok kayıtlarına
    bakıyorum…" the operator sees "Stok taradım — Logitech G Pro X 130 adetle
    önde." It sounds like the AI is REPORTING what it just saw, not narrating
    from a generic pool.
    """
    data = data or {}
    item = data.get("item") if isinstance(data.get("item"), dict) else None
    items = data.get("items") if isinstance(data.get("items"), list) else []

    if intent == "top_stock_product" and item:
        return f"Stok kayıtlarını taradım — **{item.get('name','—')}** {item.get('stock','?')} adetle önde."
    if intent == "low_stock_products" and items:
        first = items[0] if items else {}
        return f"Düşük stoklu {len(items)} ürün öne çıktı — en kritik: **{first.get('name','—')}** ({first.get('stock','?')})."
    if intent == "top_selling_product" and item:
        return f"Satış sıralamasını çıkardım — **{item.get('name','—')}** {item.get('sales','?')} adetle önde."
    if intent == "top_n_selling" and items:
        return f"Top {len(items)} satış kalemini çıkardım."
    if intent == "sales_drop_diagnosis":
        causes = data.get("causes") or []
        if causes:
            return f"Olası {len(causes)} sebebi karşılaştırdım: {', '.join(causes[:3])}."
    if intent == "sales_overview":
        total = data.get("total_sales")
        if total is not None:
            return f"Toplam satışı topladım — {total} adet."
    if intent == "sentiment_status":
        counts = data.get("counts") or {}
        if counts:
            neg = counts.get("negative", 0)
            tot = sum(counts.values()) or 1
            return f"Yorum dağılımını çıkardım — {neg}/{tot} olumsuz."
    if intent == "shipping_health":
        d = data.get("delayed")
        t = data.get("total")
        if d is not None and t is not None:
            return f"Kargo durumunu çıkardım — {d}/{t} siparişte gecikme."
    if intent == "workflows_summary":
        ac = data.get("active_count")
        if ac is not None:
            return f"Aktif iş akışlarını saydım — {ac} tane çalışıyor."
    if intent == "approval_bottlenecks":
        pending = data.get("pending") or []
        return f"Onay kuyruğuna baktım — {len(pending)} bekleyen kayıt."
    if intent == "campaigns_performance":
        camps = data.get("campaigns") or []
        return f"Son {len(camps)} kampanya sonucunu karşılaştırdım."
    if intent == "memory_patterns":
        pats = data.get("patterns") or []
        return f"Hafızadaki {len(pats)} örüntüye baktım."
    if intent == "operational_pressure":
        lvl = data.get("pressure_level")
        if lvl:
            return f"Operasyonel baskıyı ölçtüm — {lvl}."
    return None


def _retrieval_qualifier_stage(intent: str | None, data: dict | None) -> str | None:
    """Optional 2nd stage line: a qualifier that adds depth — e.g.
    'Geçmiş yorumlarla karşılaştırıyorum…' for sentiment, etc.
    """
    data = data or {}
    if intent == "sales_drop_diagnosis":
        sigs = data.get("signals") or {}
        nonzero = [k for k, v in sigs.items() if v]
        if nonzero:
            return f"Sinyalleri eşliyorum — {', '.join(nonzero[:3])}."
    if intent == "sentiment_status" and data.get("tone"):
        return f"Genel duygu tonu: {data['tone']} olarak okuyorum."
    if intent == "operational_pressure":
        notes = data.get("notes") or []
        if notes:
            return f"Öne çıkan baskı kaynakları: {', '.join(notes[:3])}."
    if intent == "top_selling_product":
        item = data.get("item") if isinstance(data.get("item"), dict) else {}
        if (item.get("stock") or 0) < 10:
            return f"Stok seviyesi {item.get('stock')} — tükenme yakın."
    if intent == "top_stock_product":
        item = data.get("item") if isinstance(data.get("item"), dict) else {}
        sales = item.get("sales") or 0
        if sales <= 5:
            return f"Satış hareketi {sales} — ivme zayıf."
    return None


def compose_stages(
    *,
    intent: str | None,
    retrieval_data: dict | None,
    is_followup: bool = False,
    inherited_label: str | None = None,
    rng: random.Random | None = None,
) -> list[str]:
    """Return 2–3 deliberation lines.

    Strategy:
        - If we can extract a concrete entity/metric from the retrieval,
          the first stage REPORTS what was found (not what is being looked
          up). This is what makes the deliberation feel alive.
        - If we can't (or for follow-ups), fall back to a generic pool so
          the panel still shows movement.
    """
    rng = rng or random.Random()
    stages: list[str] = []

    if is_followup and inherited_label:
        stages.append(f"Bağlamı koruyorum — {inherited_label} üzerinden devam ediyorum…")

    primary = _data_aware_first_stage(intent, retrieval_data)
    if primary:
        stages.append(primary)
    else:
        pool = _STAGE_POOLS.get(intent or "") or _GENERIC_OPENERS
        stages.append(rng.choice(pool))

    qualifier = _retrieval_qualifier_stage(intent, retrieval_data)
    if qualifier:
        stages.append(qualifier)

    if len(stages) < 3:
        stages.append(rng.choice(_GENERIC_CLOSERS))

    # de-duplicate while preserving order
    seen, out = set(), []
    for s in stages:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out[:4]


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


_SYSTEM_PROMPT = """Sen Türkçe konuşan, bir e-ticaret mağazasının iş danışmanısın. Mağaza sahibiyle sohbet ediyorsun — veriye hâkimsin, işin ehlisin, lakin sade ve anlaşılır konuşursun.

1) VERİ TEK KAYNAK — DATA NE DİYORSA O:
- Cevabın tamamen sana verilen `data` alanından çıkar. Orada olmayan hiçbir şeyi söyleme.
- Bir mağazada ürün olup olmadığını söylemek için o mağazaya ait ürün kaydı data'da geçiyor olmalı. Geçmiyorsa "bu mağazada ürün yok" de — başka mağazaları karıştırma.
- `data` ilgili konuda boşsa açıkça "bu konuda elimde veri yok" de. Uydurma, tahmin etme.
- Bilmediğini söylemek, uydurmaktan her zaman daha iyidir.

2) SAYILAR — SANA NE GELDİYSE O KADAR:
- Sana kaç mağaza geliyorsa AYNEN o kadar. Listedeki uzunluğu sen say, başka rakam üretme.
- Aynı şey ürünler, yorumlar, siparişler için geçerli.
- Aynı oturumda tutarsız sayı söyleme.

3) SORUYU DİREKT CEVAPLA:
- Kullanıcı ne sorduysa onu cevapla. Sormadığı şeyleri anlatma.
- "3 mağazan var, 2 ürünün var" gibi tekrarları her cevapta yapma — kullanıcı bunu zaten biliyor.
- Genel soru = tüm veriyi kapsayan özet. Spesifik soru = direkt o konunun cevabı.

4) ÜSLUP — DOĞAL, AKICI, İŞİN EHLİ:
- Samimi, sade, akıcı Türkçe. Ancak, lakin, ama, yani, üstelik, oysa, bununla birlikte gibi bağlaçları doğal akışta kullan.
- Kısa ve net cevaplar. Gereksiz giriş yok, kapanış kalıbı yok.
- Rakamları ve ürün isimlerini cümle içine yedir — madde işareti veya liste yapma.
- Jargon YOK: "operasyonel", "sinerji", "optimize", "KPI", "funnel", "engagement", "satış potansiyeli" geçmez.
- Kalıp YOK: "Elbette", "tabii ki", "harika", "umarım yardımcı olur", "İstersen şunu yapabilirim", "oldukça iyi", "dikkat çekiyor" gibi kalıplar kullanma.

5) KONUŞMA TARZI:
- Önce genel bir değerlendirme yap, sonra detaya in. Örnek: "Genel olarak ikisi de iyi durumda, ancak aralarında fark var."
- Rakamları karşılaştırmalı anlat. "Razer'da marj %35, Aula'da %32" — tek tek sıralama değil, karşılaştırma.
- Çelişki veya nüans varsa onu da söyle. Örnek: "Birim karda Aula önde ama marj oranında Razer daha iyi — ikisi farklı şey söylüyor."
- Önerin varsa gerekçesiyle ver. "Razer'ı öneririm çünkü rating'i daha yüksek ve yorumları daha temiz."
- Veri eksikse dürüstçe söyle. "Haftalık satış verisi olmadan kesin bir şey söylemek zor."

6) META SORULAR — KENDİN HAKKINDA SORULAR:
- "Sana güvenebilir miyim?" → Sadece veri ne diyorsa onu söylediğini, veri yoksa icat etmediğini kısaca açıkla.
- "Bana yalan söylüyor musun?" → Hayır, sadece verilen data'ya göre konuştuğunu söyle. Savunmaya geçme, kısa tut.
- "Nasılsın?", "Sen kimsin?" gibi sorular → Kısaca kim olduğunu söyle, hemen konuya dön.

7) MARKDOWN YASAK:
- **bold** kullanma. *italic* kullanma. Başlık (#) kullanma. Madde işareti (- veya *) kullanma.
- Düz metin yaz. Vurgulamak istediğin şeyi cümle yapısıyla vurgula, işaretle değil.

8) KAR MARJI VE FİYAT ANALİZİ:
- Ürünün fiyatı ve maliyet fiyatı verilmişse kar ve marjı hesapla: kar = fiyat - maliyet, marj = kar/fiyat * 100.
- Hem marj oranını hem birim karı karşılaştır — ikisi farklı şey söylüyor olabilir, bunu kullanıcıya anlat.
- İndirim önerisi yaparken somut yeni fiyatı söyle. "Aula'ya %10 indirim yapsan 2780 TL'ye iner."

9) ÖNERİ VER — SOMUT VE VERİYE DAYALI:
- Somut sayı ve ürün adıyla öneri yap, gerekçesiyle birlikte ver.
- Genel tavsiye verme: "Kampanya başlatabilirsin", "Sosyal medyada paylaş", "Pazarlama stratejini güçlendirebilirsin" yasak.
- Veri yoksa öneri yapma.

10) KULLANICI YORUMLARINI AKTARIRKEN:
- Türkçe yazım hatalarını sessizce düzelt, anlam ve tonu koru.
- Özetleme, kısaltma yapma — sadece imlayı toparla.

11) GENEL SORULARDA BAĞLAM MİRASI YASAK:
- Genel soru geldiğinde önceki turda konuşulan tek ürüne yapışma, tüm veriyi tara.
- Önceki bağlamı sadece kullanıcı açıkça o konuyu kastediyorsa taşı.

FORBIDDEN_PHRASES listesindeki ifadeleri ASLA kullanma.

Çıktı: SADECE düz Türkçe metin. Markdown yok, bold yok, başlık yok, madde işareti yok."""


# Phrases the synthesizer must avoid. Seeded from the codebase's known canned
# templates — these are the patterns operator users complain about. The list
# is sent to every LLM call so even Turn 1 of a fresh session is protected.
_FORBIDDEN_PHRASES: tuple[str, ...] = (
    "küçük bir indirim kampanyası",
    "kısa bir trend analizi",
    "açıklayıcı bir paylaşım ve destek akışı",
    "kargo süreci için bir bilgilendirme",
    "bekleyen müşteri sorularına bir cevap akışı",
    "düşük stoklu ürünler için bir hatırlatma",
    "büyütme odaklı bir Instagram paylaşımı",
    "yeni bir kampanya akışı",
    "İstersen şunları hazırlayabilirim",
    "İstersen kısa bir trend analizi çalıştırabilirim",
    "İstersen:",
    "hazırlayabilirim.",
    "Bu adımlar, ",
    "Bu durumu düzeltmek adına şu adımları",
    "satış potansiyeli",
    "oldukça iyi",
    "dikkat çekiyor",
    "dikkat çekici",
    "göz önünde bulundurulduğunda",
    "pazarlama stratejinizi güçlendirebilirsiniz",
    "pazarlama stratejinizi",
    "bu özellikleri öne çıkararak",
    "bu da alıcıları çekmek için",
    "bu da talebi artırabilir",
)


# Map intent codes → operator-language *context* (NOT prescriptive phrasing).
# This describes what the intent IS, so the LLM can decide whether and how to
# weave it into prose. Compared to the old _INTENT_SUGGESTIONS, these are
# descriptors, not finished sentences the LLM might echo verbatim.
_INTENT_CONTEXT: dict[str, str] = {
    "discount_promotion":   "fiyat / indirim odaklı bir aksiyon türü",
    "growth_marketing":     "büyüme / görünürlük aksiyonu (sosyal medya, banner)",
    "marketing_campaign":   "genel pazarlama kampanyası",
    "reputation":           "itibar onarımı (açıklama, destek, telafi)",
    "shipping_response":    "müşteriye kargo bilgilendirmesi",
    "customer_support":     "doğrudan müşteri destek yanıtı",
    "inventory_review":     "stok analizi / yenileme tetiği",
    "insights":             "iç analiz (trend, sebep, performans)",
    "low_stock_alert":      "düşük stok uyarısı (kritik)",
    "store_welcome":        "yeni mağaza karşılama akışı",
    "general_marketing":    "spesifik olmayan pazarlama dokunuşu",
}


def compose_prompt(
    *,
    question: str,
    resolved_question: str,
    retrieval: dict,
    memory_ctx: dict,
) -> list[dict]:
    """Build a chat-completion message list.

    We deliberately do NOT pass the deterministic `answer` field or the
    canned `suggestion` strings — those leak template phrasing the operator
    keeps complaining about. Instead we pass:
      - the structured `data` block (numbers, names, ratios)
      - intent codes + neutral context descriptors (not finished sentences)
      - the operator's history + active subject
      - the global forbidden-phrases list AND any session-level anti-phrases
    """
    # Convert prescriptive `recommendations[].suggestion` into intent codes
    # only. Description is intentionally generic so the LLM has to *compose*
    # the action sentence from the data, not echo a static phrase.
    recs = retrieval.get("recommendations") or []
    intent_options = []
    for r in recs:
        code = r.get("intent")
        if not code:
            continue
        intent_options.append({
            "intent_code": code,
            "context": _INTENT_CONTEXT.get(code, "operasyonel aksiyon türü"),
        })

    facts = {
        "intent":           retrieval.get("intent") or retrieval.get("routed_intent"),
        "confidence":       retrieval.get("confidence"),
        "data":             retrieval.get("data") or {},
        # NOTE: no `baseline_answer` here — that's what was leaking templates.
        "available_action_intents": intent_options,
    }

    history = memory_ctx.get("history") or []
    anti_phrases = list(memory_ctx.get("anti_phrases") or [])

    # Always extend with the global forbidden seed — first-turn protection.
    forbidden = list(_FORBIDDEN_PHRASES)

    history_lines: list[str] = []
    for h in history[-4:]:
        q = h.get("resolved_question") or h.get("question") or ""
        ent = h.get("entity") or ""
        if q:
            line = f"  - kullanıcı sordu: {q[:120]}"
            if ent:
                line += f" (konu: {ent})"
            history_lines.append(line)

    history_block = "\n".join(history_lines) if history_lines else \
        "  - (bu konuşmada henüz başka tur yok)"

    facts_block = json.dumps(facts, ensure_ascii=False, indent=2)[:3500]

    # PG ürün/yorum/mağaza verisi — business_query_router'dan gelir
    data = retrieval.get("data") or {}
    pg_block = format_pg_context(data.get("pg_context", {}))
    op_block = format_op_context(data.get("op_context", {}))

    if anti_phrases:
        session_anti_block = ", ".join('"' + p + '"' for p in anti_phrases[:10])
    else:
        session_anti_block = "(bu oturumda henüz tekrar tespit edilmedi)"

    forbidden_block = "\n".join(f"  • \"{p}\"" for p in forbidden)

    # PG ve operasyonel blokları sadece doluysa ekle
    pg_section = (
        f"ÜRÜN / MAĞAZA VERİSİ (PostgreSQL — gerçek kayıtlar):\n{pg_block}\n\n"
        if pg_block else ""
    )
    op_section = (
        f"OPERASYONEL VERİ (stok, satış, kargo, workflow):\n{op_block}\n\n"
        if op_block else ""
    )

    user_block = (
        "KULLANICI SORUSU (ham):\n"
        f"{question}\n\n"
        "KULLANICI SORUSU (çözümlenmiş):\n"
        f"{resolved_question}\n\n"
        f"{pg_section}"
        f"{op_section}"
        "DİĞER BULGULAR (routed retrieval verisi):\n"
        f"{facts_block}\n\n"
        "ÖNCEKİ KONUŞMA (son 4 turdan):\n"
        f"{history_block}\n\n"
        "FORBIDDEN_PHRASES (bu ifadeleri ASLA kullanma, niyetlerini farklı sözcüklerle kur):\n"
        f"{forbidden_block}\n\n"
        "ANTİ-TEKRAR — geçmiş oturum başlangıç kalıpları (bu kelimelerle CÜMLE KURMA):\n"
        f"{session_anti_block}\n\n"
        "ÖNEMLİ TALİMAT: Yukarıdaki veriden bir aksiyon türetebiliyorsan, somut ürün/sayı/kanal "
        "vererek doğal bir cümle içine yedir. Eğer türetemiyorsan aksiyon önermeyi tamamen atla — "
        "asla genel 'şunu yapabilirim' listesi yazma.\n\n"
        "Şimdi yukarıdaki veriye dayanarak operatöre kısa, doğal Türkçe bir yanıt yaz."
    )

    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user",   "content": user_block},
    ]


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------


@dataclass
class SynthesisResult:
    answer: str
    mode: str            # "llm" | "deterministic_fallback"
    latency_ms: int
    stages: list[str]
    model: str | None
    error: str | None = None


def _has_openai_key() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def synthesize_with_openai(messages: list[dict]) -> tuple[str, str | None]:
    """Returns (answer_text, model_id). Raises on any failure."""
    from openai import OpenAI

    client = OpenAI(timeout=CHAT_LLM_TIMEOUT)
    completion = client.chat.completions.create(
        model=CHAT_LLM_MODEL,
        messages=messages,
        temperature=CHAT_LLM_TEMPERATURE,
        max_tokens=CHAT_LLM_MAX_TOKENS,
    )
    msg = completion.choices[0].message
    text = (msg.content or "").strip()
    return text, completion.model


# ---------------------------------------------------------------------------
# Public synthesize()
# ---------------------------------------------------------------------------


def _deterministic_fallback(retrieval: dict) -> str:
    """When LLM is unavailable, return the baseline retrieval answer."""
    return (retrieval or {}).get("answer", "Şu an cevap üretemiyorum.")


def synthesize(
    *,
    question: str,
    resolved_question: str,
    retrieval: dict,
    memory_ctx: dict,
    is_followup: bool = False,
    inherited_label: str | None = None,
    rng_seed: int | None = None,
) -> SynthesisResult:
    """Run the synthesis pipeline. Always returns a SynthesisResult."""
    started = time.monotonic()

    rng = random.Random(rng_seed) if rng_seed is not None else None
    stages = compose_stages(
        intent=retrieval.get("routed_intent") or retrieval.get("intent"),
        retrieval_data=retrieval.get("data"),
        is_followup=is_followup,
        inherited_label=inherited_label,
        rng=rng,
    )

    if not CHAT_USE_LLM or not _has_openai_key():
        return SynthesisResult(
            answer=_deterministic_fallback(retrieval),
            mode="deterministic_fallback",
            latency_ms=int((time.monotonic() - started) * 1000),
            stages=stages,
            model=None,
            error=None if _has_openai_key() else "no_openai_key",
        )

    messages = compose_prompt(
        question=question,
        resolved_question=resolved_question,
        retrieval=retrieval,
        memory_ctx=memory_ctx,
    )

    try:
        text, model_id = synthesize_with_openai(messages)
        if not text:
            raise RuntimeError("empty completion")
        return SynthesisResult(
            answer=text,
            mode="llm",
            latency_ms=int((time.monotonic() - started) * 1000),
            stages=stages,
            model=model_id,
            error=None,
        )
    except Exception as exc:
        print(f"[AI_SYNTH] LLM call failed, falling back: {exc}")
        return SynthesisResult(
            answer=_deterministic_fallback(retrieval),
            mode="deterministic_fallback",
            latency_ms=int((time.monotonic() - started) * 1000),
            stages=stages,
            model=None,
            error=str(exc)[:200],
        )