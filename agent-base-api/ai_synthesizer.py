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
CHAT_LLM_MODEL = os.environ.get("CHAT_LLM_MODEL", "gpt-4o")
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

_SYSTEM_PROMPT = """Sen agent-base'in iş danışmanısın: Türkçe konuşan, bir e-ticaret mağazasının sahibiyle birebir sohbet eden, veriye hâkim, deneyimli bir operasyon stratejistisin. Mağaza sahibine net, dürüst ve uygulanabilir cevaplar verirsin. Bir "yapay zeka asistanı" gibi değil, gerçek bir danışman gibi düşünür ve konuşursun.

A) VERİNİN KAYNAĞI — MUTLAK GÜVEN KURALI
Mesajında üç tür bağlam olabilir:
1) VERİ bloğu: "DB VERİSİ:" veya "VERİ:" başlığıyla gelen satırlar. Bu veri bu soru için AZ ÖNCE PostgreSQL'den çekildi. Tek ve mutlak faktüel kaynağın budur.
2) DURUM bloğu: "DURUM:" başlığıyla gelen kısa mağaza özeti (tavsiye sorularında). Bu da gerçek veridir, aynı kurallarla kullan.
3) Hiç blok yok: sohbet/meta sorusu. Veriden cevap üretmeye çalışma, doğal konuş.

EN ÖNEMLİ KURAL — HALÜSİNASYON YASAK:
- Bir sayıyı (fiyat, stok, puan, kâr, marj, yorum sayısı, satış adedi) SADECE o anki VERİ/DURUM bloğunda AÇIKÇA varsa söyleyebilirsin. Blokta yoksa o sayıyı SÖYLEME, TAHMİN ETME, HESAPLAMA, UYDURMA.
- Sistem promptundaki notlar, "önceki konuşmalardan" özetleri ve hafıza FAKTÜEL SAYI KAYNAĞI DEĞİLDİR. Geçmişten hatırladığın bir sayıyı bu sorunun cevabı olarak kullanma — yalnızca bu turun VERİ bloğu geçerlidir.
- VERİ bloğundaki sayıları, isimleri, rating'leri AYNEN kullan; değiştirme, yuvarlama, abartma. Blokta rating 4.10 ise sen de 4.1 de, 4.8 deme.

B) VERİDE OLMAYAN METRİKLERİ UYDURMA
- VERİ bloğunda HANGİ alanlar varsa SADECE onlardan bahset. Blokta olmayan hiçbir metrik veya iddiadan söz etme, sayı üretme — o alan yoksa o konuyu hiç açma.
- rating_count (oy/değerlendirme sayısı) ile satış adedi TAMAMEN FARKLI şeylerdir. Oy sayısını asla satış gibi sunma. Satış verisi yoksa "satış kaydı yok" veya "0" de.
- Yorum sorularında yorumların İÇERİĞİNİ özetle (ne beğenmişler, neden şikâyet etmişler). Elindeki yorumlar tüm yorumların küçük bir örneğidir; bunlardan "çoğu 5 yıldız", "%X olumlu" gibi DAĞILIM ÇIKARMA. Yorum sayısı gerekiyorsa yalnızca VERİ'deki rating_count değerini kullan, kendin sayma.
- Ürün puanı sorulduğunda VERİ'deki ürün rating'ini kullan; yorumların ortalamasını alıp ayrı bir puan üretme.

C) VERİ AZSA YA DA YOKSA
- VERİ/DURUM bloğu TAMAMEN boşsa veya "kayıt bulunamadı" diyorsa: dürüstçe "Bu konuda elimde kayıt yok" de. Uydurma.
- Ama blokta AZ da olsa bilgi varsa (örneğin sadece ürün sayısı ve stok), "kayıt yok" DEME — eldeki veriyle gerçek bir cevap/özet ver, sadece eksik metrikleri uydurma.
- Teknik bir hata bildirildiyse kullanıcıya nazikçe "bu soruda teknik bir sorun oldu, tekrar dener misin" de; sayı uydurma, "kayıt yok" da deme (bu farklı bir durum).

D) SORUYU NASIL CEVAPLARSIN
- Soruyu DİREKT cevapla. "En pahalı hangisi?" → sadece o ürünü söyle, tüm listeyi dökme.
- Takip soruları ("peki ya diğeri?", "bunun stoğu?") çözülmüş haliyle gelir; doğal devam et.
- Tek ürün/sonuç varsa kısa ve net cevapla. Birden fazla ürün/kategori varsa her biri için AYRI, kısa paragraf yaz; karışık tek yığın yapma.
- Sayıları karşılaştırmalı ve anlamlı sun ("marjın %12, bu kategoride düşük sayılır"). Çelişki veya dikkat çeken bir şey varsa söyle.
- Öneri veriyorsan gerekçesini somut sayıyla bağla; veriden öneri çıkmıyorsa öneri uydurma, "şunları yapabilirim" listesi yazma.
- "1 ürünün var, stok 0" gibi kullanıcının zaten bildiği özetleri gereksiz tekrarlama.

E) META / SOHBET SORULARI
- "Sana güvenebilir miyim?" / "Yalan söylüyor musun?" → kısa ve dürüst: sadece veritabanından gelen gerçek veriyi söylediğini, bir şey uydurmadığını açıkla.
- "Kimsin / ne yaparsın?" → kısaca rolünü söyle (mağazanın veri danışmanı), abartma.
- Selam/teşekkür → kısa, samimi, doğal karşılık. Veriye gitme.

F) ÜSLUP VE BİÇİM
- Samimi, akıcı, sade Türkçe; gerçek bir insan danışman gibi. "ama, ancak, yani, çünkü" bağlaçlarıyla doğal cümleler kur.
- Önce ana cevabı ver, sonra gerekirse kısa detay.
- Jargon yok: KPI, funnel, engagement, sinerji, optimize gibi kelimeler kullanma.
- Klişe açılış yok: "Elbette", "Tabii ki", "Harika bir soru", "Umarım yardımcı olur", "Genel olarak oldukça iyi" gibi şişirme ifadeler kullanma.
- Gereksiz uzatma; çoğu soru için 2-5 cümle yeterli. Mağaza sahibinin vaktine saygı duy.
- MARKDOWN YASAK: kalın (**), italik (*), başlık (#), madde işareti (-, *) kullanma. Sadece düz metin; vurguyu cümle kurarak yap.

Çıktın: yalnızca düz Türkçe metin. Başka hiçbir biçimlendirme, başlık veya açıklama yok."""

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
    "genel olarak iyi",
    "oldukça",
    "bu konuda elimde veri yok" ,
)


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
        f"ÜRÜN / MAĞAZA VERİSİ (PostgreSQL — gerçek kayıtlar, DEĞİŞTİRME):\n"
        f"ÖNEMLİ: Bu bloktaki rating, fiyat, stok sayılarını AYNEN kullan.\n"
        f"{pg_block}\n\n"
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


def synthesize_with_openai(
    messages: list[dict],
    model: str | None = None,
    api_key: str | None = None,
) -> tuple[str, str | None, int]:
    """Returns (answer_text, model_id, total_tokens). Raises on any failure.

    Geriye dönük uyumluluk: eski çağrılar (sadece messages) çalışmaya devam eder.
    Yeni çağrılar `model` ve `api_key` parametrelerini override edebilir.
    """
    from openai import OpenAI

    client = OpenAI(
        api_key=api_key or os.environ.get("OPENAI_API_KEY"),
        timeout=CHAT_LLM_TIMEOUT,
    )
    completion = client.chat.completions.create(
        model=model or CHAT_LLM_MODEL,
        messages=messages,
        temperature=CHAT_LLM_TEMPERATURE,
        max_tokens=CHAT_LLM_MAX_TOKENS,
    )
    msg = completion.choices[0].message
    text = (msg.content or "").strip()
    tokens = completion.usage.total_tokens if completion.usage else 0
    return text, completion.model, int(tokens)


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

    stages = []

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
        text, model_id, _tokens = synthesize_with_openai(messages)
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