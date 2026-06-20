"""
AI Operator chat — multi-turn cognition over real retrieved data.

Pipeline per turn:

    open or load session
       │
       ▼
    resolve follow-up references         (conversation_memory.resolve_follow_up)
       │                                  e.g. "peki neden?" → "Satışlar neden bu durumda?"
       ▼
    route resolved question → retrieval  (business_query_router → business_retrieval_service)
       │                                  fact-grounded data
       ▼
    open-ended fallback if no route      (narrative_synth deterministic narrative)
       │
       ▼
    synthesize natural Turkish prose     (ai_synthesizer — LLM-backed; fallback to baseline)
       │
       ▼
    record turn                          (conversation_memory.record_turn)
       │                                  → updates anti-phrase list + active entity

The chat output now includes a `stages` array (deliberation lines for the
UI) and a `mode` field ("llm" | "deterministic_fallback") so the dashboard
can show what shape the answer took.

This module never queries the database directly. It composes the
retrieval + memory + synthesis layers.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

import ai_synthesizer
import business_query_router as query_router
import conversation_memory as memory
from business_intelligence import analyze
from business_state import build_business_state
from context_compressor import compress_timeline
from cross_event_reasoner import reason_across_events
from db import execute_query
from narrative_synth import synthesize_narrative
from planner_learning import get_learning_summary
from planner_memory import get_memory_summary_for_api
from timeline_service import fetch_timeline
from campaign_intent import parse_campaign_intent as parse_campaign_intent_llm
import product_resolver


# Hints that explicitly want a broad summary instead of a specific answer.
_OPEN_ENDED_HINTS = (
    "öneri", "tavsiye", "strateji",
    "genel durum", "özet", "ne durumda", "summary", "overview",
    "neler oluyor",
)


def _is_open_ended(question: str) -> bool:
    q = (question or "").lower()
    return any(h in q for h in _OPEN_ENDED_HINTS)


def _fetch_workflows(user_id: int, limit: int = 15) -> list[dict]:
    rows = execute_query(
        """
        SELECT workflow_name, status, created_at, entity_type, entity_id
        FROM workflow_instances WHERE user_id=? ORDER BY id DESC LIMIT ?
        """,
        (user_id, limit),
    )
    return [dict(r) for r in rows]


def _coerce_entity_id(raw):
    """int'e çevrilebiliyorsa int döndür, çevrilemezse string olarak bırak.

    Legacy SQLite ürünleri integer id taşır; yeni PG ürünleri UUID string.
    Bu yardımcı her ikisini de bozulmadan korur.
    """
    try:
        return int(raw)
    except (TypeError, ValueError):
        return str(raw)


def _entity_from_retrieval(retrieval: dict) -> tuple[str | None, int | str | None, str | None]:
    """Extract (type, id, human_label) from a retrieval payload, if present.

    id alanı int (legacy SQLite) veya UUID string (PG products) olabilir;
    _coerce_entity_id ikisini de güvenli şekilde geçirir.
    """
    data = (retrieval or {}).get("data") or {}
    item = data.get("item")
    if isinstance(item, dict) and item.get("id"):
        return "item", _coerce_entity_id(item["id"]), item.get("name")
    items = data.get("items")
    if isinstance(items, list) and items:
        first = items[0]
        if isinstance(first, dict) and first.get("id"):
            return "item", _coerce_entity_id(first["id"]), first.get("name")
    return None, None, None


def _pg_product_overview(user_id: int) -> dict:
    """Faz 6: PG products tablosundan ürün özetini al.

    Eski fake_ai_api.db/listener.db `items` tablosu yerine — open-ended
    fallback'in state_summary ve narrative_synth'e besledii ürün verisi
    artık gerçek PG kaynaklı. Hata olursa boş dict döner; çağıran taraf
    eski state'i geçirmeye devam eder (graceful degradation).
    """
    try:
        from app.core.database import SessionLocal
        from app.models.product import Product
        from app.models.store import Store
        # SQLAlchemy string-based relationship'ler için (mapper registry)
        from app.models.product_image import ProductImage  # noqa: F401
        from app.models.product_review import ProductReview  # noqa: F401
        from app.models.product_faq import ProductFaq  # noqa: F401
        from app.models.product_metrics_weekly import ProductMetricsWeekly  # noqa: F401
        from sqlalchemy import func, select

        with SessionLocal() as session:
            count_stmt = (
                select(func.count())
                .select_from(Product)
                .join(Store, Product.store_id == Store.id)
                .where(Store.user_id == int(user_id))
            )
            total_products = int(session.scalar(count_stmt) or 0)

            stock_stmt = (
                select(func.coalesce(func.sum(Product.stock), 0))
                .select_from(Product)
                .join(Store, Product.store_id == Store.id)
                .where(Store.user_id == int(user_id))
            )
            total_stock = int(session.scalar(stock_stmt) or 0)

            weekly_sum_stmt = (
                select(func.coalesce(func.sum(Product.weekly_sales), 0))
                .select_from(Product)
                .join(Store, Product.store_id == Store.id)
                .where(Store.user_id == int(user_id))
            )
            total_weekly_sales = int(session.scalar(weekly_sum_stmt) or 0)

            # Düşük stok eşiği 10 (business_state ile aynı default)
            low_rows = list(
                session.scalars(
                    select(Product)
                    .join(Store, Product.store_id == Store.id)
                    .where(Store.user_id == int(user_id))
                    .where(Product.stock != None)  # noqa: E711
                    .where(Product.stock < 10)
                    .limit(5)
                ).all()
            )
            low_stock_items = [
                {"id": str(p.id), "name": p.name, "stock": p.stock}
                for p in low_rows
            ]

            top_rows = list(
                session.scalars(
                    select(Product)
                    .join(Store, Product.store_id == Store.id)
                    .where(Store.user_id == int(user_id))
                    .order_by(
                        Product.weekly_sales.desc().nulls_last(),
                        Product.rating_count.desc().nulls_last(),
                        Product.created_at.desc(),
                    )
                    .limit(5)
                ).all()
            )
            top_products = [
                {
                    "id": str(p.id),
                    "name": p.name,
                    "sales": int(p.weekly_sales) if p.weekly_sales is not None else 0,
                    "stock": p.stock,
                    "rating": float(p.rating) if p.rating is not None else None,
                }
                for p in top_rows
            ]

            return {
                "total_products": total_products,
                "total_stock_units": total_stock,
                "total_item_sales": total_weekly_sales,
                "top_products": top_products,
                "low_stock_items": low_stock_items,
            }
    except Exception as exc:
        print(f"[CHAT] PG product overview error: {exc}")
        return {}


def _build_open_ended_retrieval(question: str, user_id: int) -> dict:
    """When the router returns None, fall back to a state-snapshot 'retrieval'
    shape so the synthesizer still gets a single, structured fact bundle.
    """
    state = build_business_state(user_id)
    timeline_data = fetch_timeline(limit=30, direction="desc")
    events = timeline_data.get("data", [])
    cross = reason_across_events(user_id)
    workflows = _fetch_workflows(user_id)

    # Faz 6: state'in ürün-bazlı alanlarını PG products tablosundan override
    # et — narrative_synth ve state_summary artık eski items tablosu yerine
    # gerçek PG sayımını görür ("843 ürün", "Seramik Vazo" sızıntıları biter).
    pg = _pg_product_overview(user_id)
    if pg:
        low_count = len(pg.get("low_stock_items") or [])
        state["inventory"] = {
            **(state.get("inventory") or {}),
            "total_products": pg.get("total_products", 0),
            "total_stock_units": pg.get("total_stock_units", 0),
            "low_stock_count": low_count,
            "low_stock_items": pg.get("low_stock_items") or [],
            "health": "warning" if low_count else "healthy",
            "source": "pg",
        }
        state["sales"] = {
            **(state.get("sales") or {}),
            "total_item_sales": pg.get("total_item_sales", 0),
            "top_products": pg.get("top_products") or [],
            "source": "pg",
        }

    synthetic_event = {
        "id": 0, "group": "chat", "event": "query",
        "description": question,
        "payload": {"natural_language": question},
        "changes": {},
    }
    bi = analyze(synthetic_event, "chat.query", {}, user_id)

    out = synthesize_narrative(
        intent="recommendations" if _is_open_ended(question) else "general",
        state=state,
        bi=bi,
        cross=cross,
        memory={},
        workflows=workflows,
    )

    return {
        "intent": "open_ended",
        "routed_intent": "open_ended",
        "answer": out.get("narrative") or (
            "Şu an net konuşabileceğim bir sinyal görmüyorum. "
            "Daha spesifik bir soru sorarsan veriye doğrudan bakabilirim."
        ),
        "data": {
            "state_summary": {
                # PG kaynaklı (varsa); değilse build_business_state'ten
                "total_products": (state.get("inventory") or {}).get("total_products"),
                "total_stock_units": (state.get("inventory") or {}).get("total_stock_units"),
                "top_products": (state.get("sales") or {}).get("top_products") or [],
                "inventory_health": (state.get("inventory") or {}).get("health"),
                "active_workflows": (state.get("campaigns") or {}).get("active_count", 0),
                "negative_reviews": (state.get("engagement") or {}).get("negative_reviews", 0),
                "sentiment": state.get("sentiment"),
            },
            "cross_event_summary": cross.get("summary"),
            "primary_hypothesis": cross.get("primary_hypothesis"),
            "bi_insights": [
                ins.get("message", str(ins))
                for ins in (bi.get("insights") or [])[:5]
            ],
        },
        "recommendations": out.get("recommendations", []),
        "confidence": out.get("confidence", 0.55),
        "timeline_compressed": compress_timeline(events, 8),
    }


# ---------------------------------------------------------------------------
# One-shot action dispatch — LLM classifier + direct LangGraph kickoff
# ---------------------------------------------------------------------------


def _classify_intent(question: str) -> str:
    """LLM sınıflandırıcı → 'action' | 'data_query' | 'advisory' | 'chat'.

    action     : yapılması istenen komut (kampanya/story/post/banner/paylaşım/kupon).
    data_query : mevcut veriden somut bilgi (fiyat, stok, satış, yorum, müşteri, kâr).
    advisory   : veriye dayalı yorum/öneri/strateji ('ne yapmalıyım', 'öne çıkarmalı mıyım').
    chat       : veri gerektirmeyen sohbet/meta (selam, teşekkür, 'kimsin',
                 'az önce ne konuştuk', 'bana güvenebilir miyim').
    Fail → 'data_query' (güvenli varsayılan, mevcut davranış).
    """
    if not ai_synthesizer.CHAT_USE_LLM or not os.environ.get("OPENAI_API_KEY"):
        return "data_query"
    try:
        from openai import OpenAI
        client = OpenAI(timeout=8)
        completion = client.chat.completions.create(
            model=ai_synthesizer.CHAT_LLM_MODEL,
            messages=[
                {"role": "system", "content": (
                    "Bir e-ticaret operatörünün Türkçe mesajını sınıflandır. "
                    "Tam olarak şu dört etiketten BİRİNİ döndür:\n"
                    "- action: yapılması istenen komut (kampanya, story, post, banner, "
                    "paylaşım, kupon oluştur/başlat).\n"
                    "- data_query: mevcut veriden somut bilgi sorusu (fiyat, stok, satış, "
                    "yorum, puan, müşteri, kâr, kategori).\n"
                    "- advisory: veriye dayalı yorum/öneri/strateji VEYA mağazanın genel "
                    "durumunu/özetini isteyen sorular ('ne yapmalıyım', 'öne çıkarmalı "
                    "mıyım', 'hangi ürüne odaklanayım', 'genel durum nasıl', 'nasıl "
                    "gidiyor', 'mağazam ne durumda', 'özet ver').\n"
                    "- chat: veri gerektirmeyen sohbet veya meta (selamlama, teşekkür, "
                    "'kimsin', 'az önce ne konuştuk', 'bana güvenebilir miyim').\n"
                    "SADECE etiketi yaz, başka hiçbir şey yazma."
                )},
                {"role": "user", "content": question},
            ],
            temperature=0,
            max_tokens=10,
        )
        raw = (completion.choices[0].message.content or "").strip().lower()
        for label in ("action", "advisory", "data_query", "chat"):
            if label in raw:
                return label
        return "data_query"
    except Exception as exc:
        print(f"[CHAT] intent classify failed: {exc}")
        return "data_query"
    

def _light_state_summary(user_id: int) -> str:
    """Advisory cevaplar için hafif, PG-kaynaklı durum özeti (SQL üretmeden)."""
    pg = _pg_product_overview(user_id)
    if not pg:
        return ""
    parts = [
        f"Toplam ürün: {pg.get('total_products', 0)}",
        f"Toplam stok: {pg.get('total_stock_units', 0)} adet",
    ]
    low = pg.get("low_stock_items") or []
    if low:
        parts.append("Düşük stok: " + ", ".join(
            f"{i['name']} ({i['stock']})" for i in low[:3]))
    top = pg.get("top_products") or []
    if top:
        parts.append("Öne çıkan: " + ", ".join(t["name"] for t in top[:3]))
    return "DURUM:\n" + "\n".join(parts)


def _answer_chat(question, user_id, sid, api_key, *, context_data="", advisory=False) -> dict:
    """Retrieval gerektirmeyen sohbet/meta + advisory cevap. nl_to_sql'e GİTMEZ."""
    import time as _time
    user_memories = memory.get_user_memories(user_id)
    past_summaries = memory.get_session_summary(user_id, limit=2)
    sys = ai_synthesizer._SYSTEM_PROMPT
    if user_memories:
        sys += f"\n\nKullanıcı hakkında bilinen notlar:\n{user_memories}"
    if past_summaries:
        sys += f"\n\nÖnceki konuşmalardan:\n{past_summaries}"
    if advisory:
        sys += ("\n\nKullanıcı tavsiye/öneri istiyor. Aşağıdaki DURUM verisine dayan; "
                "veri yoksa dürüstçe söyle, sayı UYDURMA.")

    messages = memory.build_openai_messages(
        session_id=sid, system_prompt=sys, new_question=question,
        context_data=context_data, limit_turns=10,
    )
    t0 = _time.monotonic()
    try:
        answer_text, model_id, tokens = ai_synthesizer.synthesize_with_openai(
            messages=messages, model=ai_synthesizer.CHAT_LLM_MODEL, api_key=api_key,
        )
        if not answer_text:
            raise RuntimeError("empty completion")
        mode = "advisory" if advisory else "chat"
    except Exception as exc:
        print(f"[CHAT] chat synth failed: {exc}")
        answer_text = "Şu an cevap üretemedim, biraz sonra tekrar dener misin?"
        model_id, tokens, mode = None, 0, "deterministic_fallback"
    latency_ms = int((_time.monotonic() - t0) * 1000)
    cost = _estimate_cost(tokens, model_id or ai_synthesizer.CHAT_LLM_MODEL)

    memory.record_turn(
        session_id=sid, user_id=user_id, question=question, answer=answer_text,
        intent=mode, model_used=model_id, tokens_used=tokens, cost_usd=cost,
    )
    return {
        "question": question, "resolved_question": None, "is_followup": False,
        "follow_up_rationale": "no_retrieval", "session_id": sid,
        "intent": mode, "routed_intent": mode, "active_entity": None,
        "answer": answer_text, "stages": [f"Niyet: {mode} (retrieval yok)"],
        "data": {}, "recommendations": [], "confidence": 0.8,
        "mode": mode, "model": model_id, "latency_ms": latency_ms,
        "tokens_used": tokens, "cost_usd": cost,
        "sources": ["openai_native_history"] + (["pg_state"] if advisory else []),
        "fallback": mode == "deterministic_fallback", "anti_repetition_active": False,
        "error": None,
    }

_PRODUCT_SEARCH_STOPWORDS: set[str] = {
    "için", "icin", "ile", "gibi", "ve", "veya", "ya", "da", "de", "ki",
    "ama", "fakat", "ancak", "mi", "mı", "mu", "mü",
    "bu", "şu", "su", "o", "bir", "birkaç", "her", "hepsi", "tüm", "tum",
    "artık", "artik", "hemen", "sonra", "önce", "once", "şimdi", "simdi",
    "bugün", "bugun", "yarın", "yarin", "dün", "dun",
    "oluştur", "olustur", "yap", "hazırla", "hazirla", "yaz", "yayınla",
    "yayinla", "paylaş", "paylas", "başlat", "baslat", "gönder", "gonder",
    "ekle", "kaydet", "kur", "aç", "ac", "kapa", "kapat", "planla",
    "çek", "cek", "üret", "uret", "iste", "ister", "lütfen", "lutfen",
    "kampanya", "kampanyaya", "kampanyası", "kampanyasi",
    "indirim", "indirimi", "indirimli", "iskonto", "kupon", "fırsat",
    "firsat", "hediye", "promosyon", "duyuru",
    "story", "post", "hikaye", "paylaşım", "paylasim",
    # soru kelimeleri
    "ne", "kim", "nerede", "neden", "niye", "niçin", "nicin", "nasıl", "nasil",
    "hangi", "hangisi", "kaç", "kac", "kadar", "kaçtır", "kactir",
    # ürün'den türeyen pronoun kelimeleri (rewrite öncesi search'de bağlam taşımaz)
    "ürün", "urun", "ürünü", "urunu", "ürünün", "urunun", "ürüne", "urune",
    "üründen", "urunden", "ürünleri", "urunleri", "ürünlerin", "urunlerin",
    # ürün-attribute soru kelimeleri (product NAME aramada gürültü)
    "fiyat", "fiyatı", "fiyati", "fiyata", "fiyattan", "fiyatın", "fiyatin",
    "stok", "stoğu", "stogu", "stokta", "stoğum", "stogum", "stoğumuz", "stogumuz",
    "yorum", "yorumu", "yorumlar", "yorumları", "yorumlari",
    "puan", "puanı", "puani", "rating", "ortalama", "ort",
    "marj", "marjı", "marji", "kâr", "kar", "kazanç", "kazanc",
    "satış", "satis", "satışı", "satisi", "satılan", "satilan",
    "adet", "adedi", "tane", "tanesi",
    "durum", "durumu", "durumum", "değer", "deger", "değeri", "degeri",
    "toplam", "toplamı", "toplami", "miktar", "miktarı", "miktari",
    "ay", "hafta", "gün", "gun", "yıl", "yil",
}

_PRODUCT_SEARCH_SYNONYMS: dict[str, list[str]] = {
    "fare":     ["mouse", "fare"],
    "klavye":   ["keyboard", "klavye"],
    "kulaklık": ["headset", "headphone", "kulaklık"],
    "kulaklik": ["headset", "headphone", "kulaklik"],
    "mouse":    ["mouse", "fare"],
    "keyboard": ["keyboard", "klavye"],
    "ekran":    ["monitor", "display", "ekran"],
    "kamera":   ["camera", "webcam", "kamera"],
    "mikrofon": ["microphone", "mikrofon"],
}


def _extract_product_search_terms(question: str) -> list[str]:
    """Sorudan ürün arama terimlerini çıkar; stop-word'leri at, sinonimleri genişlet."""
    import re as _re
    text = (question or "").strip().lower()
    if not text:
        return []
    text_norm = _re.sub(r"[^\w\s]", " ", text, flags=_re.UNICODE)
    raw_words = [w for w in text_norm.split() if len(w) > 1]
    filtered = [
        w for w in raw_words
        if w not in _PRODUCT_SEARCH_STOPWORDS and not w.isdigit()
    ]
    expanded: list[str] = []
    for w in filtered:
        expanded.extend(_PRODUCT_SEARCH_SYNONYMS.get(w, [w]))
    return list(dict.fromkeys(expanded))


_WORD_BOUNDARY_CACHE: dict[str, "re.Pattern"] = {}


def _word_boundary_re(term: str) -> "re.Pattern":
    import re as _re
    p = _WORD_BOUNDARY_CACHE.get(term)
    if p is None:
        p = _re.compile(rf"\b{_re.escape(term)}\b", flags=_re.IGNORECASE | _re.UNICODE)
        _WORD_BOUNDARY_CACHE[term] = p
    return p


def _name_brand_match_count(product, terms: list[str]) -> int:
    """Word-boundary match — 'çok' substring olarak 'Çoklayıcı'da geçse de sayılmaz."""
    name_lower = (getattr(product, "name", "") or "").lower()
    brand_lower = (getattr(product, "brand", "") or "").lower()
    haystack = f"{name_lower} {brand_lower}"
    return sum(1 for t in terms if t and _word_boundary_re(t).search(haystack))


def _is_strong_match(score: int, total_terms: int) -> bool:
    """En az 2 farklı search-term name/brand'de word-boundary ile eşleşmeli
    VE toplam terimlerin en az %50'sini oluşturmalı.

    Bu çift kontrol, common-word leak'ini (örn. 1/8 skorla rastgele ürün
    seçilmesi) ve düşük-bilgili kısa sorgular için yanlış pozitifi engeller.
    """
    if total_terms <= 0:
        return False
    if score < 2:
        return False
    if score / total_terms < 0.5:
        return False
    return True


def _one_shot_find_product(user_id: int, question: str) -> dict | None:
    """Soruda geçen ürünü PG'de güvenli şekilde ara.

    Sıkı eşleşme kuralı: SADECE Product.name veya Product.brand kolonlarında
    arar; description/category match'i tek başına yeterli sayılmaz. Stop-word
    filtresi (Türkçe komut kalıbı kelimeleri) ve relevance skoru — en çok
    name/brand terimi eşleşen aday seçilir. Hiçbir name/brand match'i yoksa
    None döner; çağıran taraf "ürün bulunamadı" davranışına geçer.

    Zenginleştirme:
        - Product.images relationship → image_urls + thumb_url
        - Bağlı Store → name + logo_url + banner_url
    """
    search_terms = _extract_product_search_terms(question)
    if not search_terms:
        print("[CHAT] product search: hiç anlamlı arama terimi yok (stop-word sonrası)")
        return None

    try:
        from app.core.database import SessionLocal
        from app.models.product import Product
        from app.models.store import Store
        from app.models.product_image import ProductImage  # noqa: F401  (mapper bootstrap)
        from app.models.product_review import ProductReview  # noqa: F401  (mapper bootstrap)
        from app.models.product_faq import ProductFaq  # noqa: F401  (mapper bootstrap)
        from app.models.product_metrics_weekly import ProductMetricsWeekly  # noqa: F401  (mapper bootstrap)
        from sqlalchemy import or_, select

        with SessionLocal() as session:
            name_brand_conds = [
                or_(
                    Product.name.ilike(f"%{t}%"),
                    Product.brand.ilike(f"%{t}%"),
                )
                for t in search_terms
            ]
            candidates = list(session.scalars(
                select(Product)
                .join(Store, Product.store_id == Store.id)
                .where(Store.user_id == int(user_id))
                .where(or_(*name_brand_conds))
            ).all())

            if not candidates:
                print(
                    f"[CHAT] product search: name/brand için '{search_terms}' "
                    "ile eşleşme yok — None döndürüyorum (sahte fallback yok)"
                )
                return None

            scored = sorted(
                candidates,
                key=lambda p: _name_brand_match_count(p, search_terms),
                reverse=True,
            )
            best = scored[0]
            best_score = _name_brand_match_count(best, search_terms)
            total = len(search_terms)
            if not _is_strong_match(best_score, total):
                print(
                    f"[CHAT] product search: en iyi aday '{best.name}' "
                    f"score={best_score}/{total} — strong-match eşiğini geçemedi, None"
                )
                return None

            print(
                f"[CHAT] product search: '{best.name}' "
                f"(brand={best.brand!r}, score={best_score}/{total})"
            )

            first = {
                "id":          str(best.id),
                "name":        best.name,
                "brand":       getattr(best, "brand", None),
                "category":    getattr(best, "category", None),
                "price":       float(best.price) if best.price is not None else None,
                "description": getattr(best, "description", None),
                "store_id":    getattr(best, "store_id", None),
            }
    except Exception as exc:
        print(f"[CHAT] product search failed: {exc}")
        return None

    image_urls: list[str] = []
    thumb_url: str | None = None
    store_name: str | None = None
    store_logo_url: str | None = None
    store_banner_url: str | None = None
    try:
        from app.core.database import SessionLocal
        from app.models.product import Product
        from app.models.store import Store
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        with SessionLocal() as session:
            full = session.scalar(
                select(Product)
                .where(Product.id == first["id"])
                .options(selectinload(Product.images))
            )
            if full is not None:
                image_urls = [
                    img.url for img in (full.images or [])
                    if getattr(img, "url", None)
                ]
                thumb_url = image_urls[0] if image_urls else None

            store_id = (
                first.get("store_id")
                or (getattr(full, "store_id", None) if full is not None else None)
            )
            if store_id is not None:
                store = session.scalar(
                    select(Store).where(Store.id == store_id)
                )
                if store is not None:
                    store_name = getattr(store, "name", None)
                    store_logo_url = getattr(store, "logo_url", None)
                    store_banner_url = getattr(store, "banner_url", None)
    except Exception as exc:
        print(f"[CHAT] product/store enrichment failed: {exc}")

    return {
        "id":                first["id"],
        "name":              first["name"],
        "brand":             first.get("brand"),
        "category":          first.get("category"),
        "price":             first.get("price"),
        "description":       (first.get("description") or "")[:600],
        "image_url":         thumb_url,
        "primary_image_url": thumb_url,
        "image_urls":        image_urls,
        "store_name":        store_name,
        "store_logo_url":    store_logo_url,
        "store_banner_url":  store_banner_url,
    }


def _lookup_basic_product(user_id: int, question: str) -> dict | None:
    """Sadece id + name + brand çek — image/store enrichment yok.

    answer_question'un her turunda 'soru spesifik bir ürünü adlandırıyor mu?'
    sorusunu cevaplamak için kullanılır. _one_shot_find_product ile aynı
    stop-word + name/brand-only mantığı; ama O(1) read, ağır enrichment yok.
    """
    search_terms = _extract_product_search_terms(question)
    if not search_terms:
        return None
    try:
        from app.core.database import SessionLocal
        from app.models.product import Product
        from app.models.store import Store
        from app.models.product_image import ProductImage  # noqa: F401
        from app.models.product_review import ProductReview  # noqa: F401
        from app.models.product_faq import ProductFaq  # noqa: F401
        from app.models.product_metrics_weekly import ProductMetricsWeekly  # noqa: F401
        from sqlalchemy import or_, select

        with SessionLocal() as session:
            conds = [
                or_(
                    Product.name.ilike(f"%{t}%"),
                    Product.brand.ilike(f"%{t}%"),
                )
                for t in search_terms
            ]
            candidates = list(session.scalars(
                select(Product)
                .join(Store, Product.store_id == Store.id)
                .where(Store.user_id == int(user_id))
                .where(or_(*conds))
            ).all())
            if not candidates:
                return None
            scored = sorted(
                candidates,
                key=lambda p: _name_brand_match_count(p, search_terms),
                reverse=True,
            )
            best = scored[0]
            best_score = _name_brand_match_count(best, search_terms)
            total = len(search_terms)
            if not _is_strong_match(best_score, total):
                print(
                    f"[CHAT] _lookup_basic_product: '{best.name}' "
                    f"score={best_score}/{total} — strong-match eşiğini geçemedi, None"
                )
                return None
            return {
                "id":    str(best.id),
                "name":  best.name,
                "brand": getattr(best, "brand", None),
            }
    except Exception as exc:
        print(f"[CHAT] _lookup_basic_product failed: {exc}")
        return None


def _parse_campaign_intent(question: str) -> dict:
    """Kullanıcının doğal dil komutundan kampanya bilgilerini çıkarır.

    Döner:
        {
            "discount_pct": 10.0,          # %10 indirim
            "campaign_start": "2026-06-20", # kampanya başlangıç tarihi
            "campaign_end": "2026-07-11",   # kampanya bitiş tarihi
            "duration_days": 3,             # 3 günlük kampanya
            "product_count": 2,             # en çok satan 2 ürün
            "select_by": "top_sales",       # seçim kriteri
        }
    """
    from datetime import datetime, timedelta
    import re

    result = {
        "discount_pct": 0.0,
        "campaign_start": None,
        "campaign_end": None,
        "duration_days": None,
        "product_count": 1,
        "select_by": None,
    }

    q = (question or "").lower()

    # İndirim oranı — "%10", "yüzde 10", "10 indirim"
    m = re.search(r"%\s*(\d+)|yüzde\s+(\d+)|(\d+)\s*%|(\d+)\s*(?:indirim|iskonto)", q)
    if m:
        val = next(v for v in m.groups() if v is not None)
        result["discount_pct"] = float(val)

    # Süre — "3 günlük", "1 haftalık"
    m = re.search(r"(\d+)\s*günlük|(\d+)\s*gün\s*(?:süre|kampanya)", q)
    if m:
        val = next(v for v in m.groups() if v is not None)
        result["duration_days"] = int(val)

    m = re.search(r"(\d+)\s*haftalık|(\d+)\s*hafta", q)
    if m:
        val = next(v for v in m.groups() if v is not None)
        result["duration_days"] = int(val) * 7

    # Ürün sayısı — "en çok satan 2 ürün", "ilk 3 ürün"
    m = re.search(r"en\s+(?:çok|iyi)\s+satan\s+(\d+)|ilk\s+(\d+)\s+ürün|(\d+)\s+ürün", q)
    if m:
        val = next(v for v in m.groups() if v is not None)
        result["product_count"] = int(val)

    if "en çok satan" in q or "çok satılan" in q:
        result["select_by"] = "top_sales"
    elif "en kârlı" in q or "en karlı" in q:
        result["select_by"] = "top_margin"

    # Tarih parse — "bu ayın 20'si", "gelecek ayın 11'i"
    today = datetime.now()
    current_month = today.replace(day=1)
    next_month = (current_month + timedelta(days=32)).replace(day=1)

    # Başlangıç tarihi
    m_start = re.search(
        r"bu\s+ay[ıi]n\s+(\d+)(?:'?[ıiuü]?\s*(?:ile|ile\s+başla|den\s+itibaren))?",
        q
    )
    if m_start:
        day = int(m_start.group(1))
        try:
            result["campaign_start"] = today.replace(day=day).strftime("%Y-%m-%d")
        except ValueError:
            pass

    # Bitiş tarihi
    m_end = re.search(r"gelecek\s+ay[ıi]n\s+(\d+)", q)
    if m_end:
        day = int(m_end.group(1))
        try:
            result["campaign_end"] = next_month.replace(day=day).strftime("%Y-%m-%d")
        except ValueError:
            pass

    # Süre varsa ve başlangıç varsa bitiş hesapla
    if result["campaign_start"] and result["duration_days"] and not result["campaign_end"]:
        start = datetime.strptime(result["campaign_start"], "%Y-%m-%d")
        result["campaign_end"] = (start + timedelta(days=result["duration_days"])).strftime("%Y-%m-%d")

    # Başlangıç yoksa bugün
    if not result["campaign_start"]:
        result["campaign_start"] = today.strftime("%Y-%m-%d")
        if result["duration_days"] and not result["campaign_end"]:
            result["campaign_end"] = (today + timedelta(days=result["duration_days"])).strftime("%Y-%m-%d")

    return result


def _one_shot_find_top_products(user_id: int, count: int = 1, by: str = "top_sales") -> list[dict]:
    """En çok satan veya en kârlı N ürünü döner."""
    try:
        from app.core.database import SessionLocal
        from app.services.access_control import get_user_store_ids
        from sqlalchemy import text

        store_ids = get_user_store_ids(user_id)
        if not store_ids:
            return []

        with SessionLocal() as session:
            if by == "top_margin":
                sql = text("""
                    SELECT p.id::text, p.name, p.brand, p.category,
                           p.price, p.cost_price, p.stock_quantity,
                           p.rating, p.rating_count, p.description,
                           ROUND(((p.price - p.cost_price) / p.price * 100)::numeric, 2) AS marj
                    FROM products p
                    WHERE p.store_id = ANY(CAST(:store_ids AS uuid[]))
                      AND p.is_active = true
                      AND p.cost_price IS NOT NULL AND p.price > 0
                    ORDER BY marj DESC NULLS LAST
                    LIMIT :cnt
                """)
            else:  # top_sales
                sql = text("""
                    SELECT p.id::text, p.name, p.brand, p.category,
                           p.price, p.cost_price, p.stock_quantity,
                           p.rating, p.rating_count, p.description,
                           COALESCE(SUM(oi.quantity), 0) AS toplam_satis
                    FROM products p
                    LEFT JOIN order_items oi ON oi.product_id = p.id
                    LEFT JOIN orders o ON o.id = oi.order_id
                        AND o.status NOT IN ('cancelled','refunded')
                    WHERE p.store_id = ANY(CAST(:store_ids AS uuid[]))
                      AND p.is_active = true
                    GROUP BY p.id, p.name, p.brand, p.category,
                             p.price, p.cost_price, p.stock_quantity,
                             p.rating, p.rating_count, p.description
                    ORDER BY toplam_satis DESC NULLS LAST
                    LIMIT :cnt
                """)

            rows = session.execute(sql, {"store_ids": store_ids, "cnt": count}).fetchall()
            return [dict(r._mapping) for r in rows]
    except Exception as exc:
        print(f"[CHAT] _one_shot_find_top_products failed: {exc}")
        return []


def _one_shot_enrich_context(product_id: str, store_ids: list) -> dict:
    """Ürün için ek context çeker: marj, stok, satış trendi, rating özeti."""
    try:
        from app.core.database import SessionLocal
        from sqlalchemy import text

        with SessionLocal() as session:
            row = session.execute(text("""
                SELECT
                    p.price,
                    p.cost_price,
                    ROUND((p.price - COALESCE(p.cost_price,0))::numeric, 2) AS kar,
                    CASE WHEN p.price > 0 AND p.cost_price IS NOT NULL
                         THEN ROUND(((p.price-p.cost_price)/p.price*100)::numeric,2)
                         ELSE NULL END AS marj_yuzde,
                    p.stock_quantity AS stok,
                    p.rating,
                    p.rating_count,
                    COALESCE(SUM(oi.quantity), 0) AS bu_ay_satis
                FROM products p
                LEFT JOIN order_items oi ON oi.product_id = p.id
                LEFT JOIN orders o ON o.id = oi.order_id
                    AND o.status NOT IN ('cancelled','refunded')
                    AND o.ordered_at >= DATE_TRUNC('month', NOW())
                WHERE p.id = :pid
                GROUP BY p.id, p.price, p.cost_price, p.stock_quantity,
                         p.rating, p.rating_count
            """), {"pid": product_id}).fetchone()

            if not row:
                return {}

            r = dict(row._mapping)
            return {
                "kar": float(r["kar"]) if r["kar"] else None,
                "marj_yuzde": float(r["marj_yuzde"]) if r["marj_yuzde"] else None,
                "stok": int(r["stok"]) if r["stok"] is not None else None,
                "rating": float(r["rating"]) if r["rating"] else None,
                "rating_count": int(r["rating_count"]) if r["rating_count"] else None,
                "bu_ay_satis": int(r["bu_ay_satis"]),
            }
    except Exception as exc:
        print(f"[CHAT] _one_shot_enrich_context failed: {exc}")
        return {}


def _one_shot_positive_reviews(product_id: str, limit: int = 5) -> list[str]:
    """Ürünün rating>=4 son N yorumunun içeriğini liste olarak döner."""
    try:
        from app.core.database import SessionLocal
        from app.models.product_review import ProductReview
        from sqlalchemy import select
        with SessionLocal() as session:
            rows = list(session.scalars(
                select(ProductReview)
                .where(ProductReview.product_id == product_id)
                .where(ProductReview.rating >= 4)
                .order_by(ProductReview.id.desc())
                .limit(limit)
            ).all())
            return [(r.content or "").strip() for r in rows if r.content]
    except Exception as exc:
        print(f"[CHAT] positive reviews fetch failed: {exc}")
        return []


def _one_shot_run(question: str, user_id: int) -> dict | None:
    """Aşama B — parse → save → start_execution → disable → özet.

    Heavy import'lar (nl_rule_parser / structured_rule_engine / langgraph)
    try ile sarıldı; modüller yüklenemezse None döner ve çağıran taraf
    query yoluna düşer.
    """
    try:
        from nl_rule_parser import parse_rule
        from structured_rule_engine import save_rule, set_enabled, delete_rule
        from langgraph_engine.runtime import start_execution
        from structured_rule import TriggerSpec
    except Exception as exc:
        print(f"[CHAT] one-shot heavy imports failed: {exc}")
        return None

    # Kampanya intent parse — tarih, indirim, ürün sayısı
    campaign_intent = parse_campaign_intent_llm(question)
    discount_pct = campaign_intent.get("discount_pct") or 0.0
    campaign_start = campaign_intent.get("campaign_start")
    campaign_end = campaign_intent.get("campaign_end")
    product_count = campaign_intent.get("product_count") or 1
    select_by = campaign_intent.get("select_by")

    # Ürün seçimi — "en çok satan 2 ürün" veya tek ürün
    products_list: list[dict] = []
    if select_by and product_count > 1:
        products_list = _one_shot_find_top_products(user_id, count=product_count, by=select_by)
    elif select_by and product_count == 1:
        products_list = _one_shot_find_top_products(user_id, count=1, by=select_by)
    
    # Spesifik ürün adı varsa bul
    single_product = product_resolver.resolve_and_enrich(question, user_id)
    if single_product and not products_list:
        products_list = [single_product]
    elif not products_list:
        products_list = [single_product] if single_product else []

    # İlk ürünle devam et (çoklu ürün için loop eklenebilir)
    product = products_list[0] if products_list else None
    if product is None:
        return {
            "status":        "not_found",
            "summary":       (
                "Belirttiğin ürünü veritabanında bulamadım. "
                "Ürün adını veya markasını kontrol edip tekrar yazar mısın? "
                "Hiçbir kural oluşturulmadı."
            ),
            "rule_id":       None,
            "execution_id":  None,
            "approval_id":   None,
            "channel":       None,
            "channel_label": None,
            "product":       {},
        }
    p = product

    # Ek context: marj, stok, satış trendi
    from app.services.access_control import get_user_store_ids as _get_store_ids
    _store_ids = _get_store_ids(user_id)
    extra_ctx = _one_shot_enrich_context(p.get("id", ""), _store_ids) if p.get("id") else {}

    # Kampanya fiyatı hesapla
    base_price = float(p.get("price") or 0)
    campaign_price = round(base_price * (1 - discount_pct / 100), 2) if discount_pct and base_price else None

    reviews = _one_shot_positive_reviews(p["id"], limit=5) if p.get("id") else []

    # Açıklamayı yorumlar + kampanya bilgisiyle zenginleştir
    base_desc = p.get("description") or ""
    enrichment_parts = []
    if reviews:
        review_text = "\n".join(f"- {r}" for r in reviews[:5])
        enrichment_parts.append(f"Müşteri yorumları:\n{review_text}")
    if discount_pct:
        enrichment_parts.append(
            f"Kampanya: %{int(discount_pct)} indirim"
            + (f" | {base_price} TL → {campaign_price} TL" if campaign_price else "")
        )
    if campaign_start:
        date_range = campaign_start
        if campaign_end:
            date_range += f" - {campaign_end}"
        enrichment_parts.append(f"Kampanya tarihleri: {date_range}")
    if extra_ctx.get("marj_yuzde"):
        enrichment_parts.append(f"Kar marjı: %{extra_ctx['marj_yuzde']}")
    if extra_ctx.get("stok") is not None:
        enrichment_parts.append(f"Stok: {extra_ctx['stok']} adet")
    if extra_ctx.get("rating"):
        oy = f" ({extra_ctx['rating_count']} oy)" if extra_ctx.get("rating_count") else ""
        enrichment_parts.append(f"Rating: {extra_ctx['rating']}/5{oy}")

    if enrichment_parts:
        enriched_desc = (
            f"{base_desc}\n\n" + "\n".join(enrichment_parts)
            if base_desc else "\n".join(enrichment_parts)
        )
    else:
        enriched_desc = base_desc

    # content_generator_node aynı veriyi 3 yoldan okuyor: outer event,
    # event.payload flat alanlar, event.payload.item nested. Path A
    # (listener) outer + nested koyuyor; biz de paritesi için üçünü de
    # dolduruyoruz, böylece referans görsel pipeline'a mutlaka ulaşıyor.
    item_obj = {
        "name":              p.get("name"),
        "price":             p.get("price"),
        "brand":             p.get("brand"),
        "category":          p.get("category"),
        "description":       p.get("description"),
        "image_url":         p.get("image_url"),
        "primary_image_url": p.get("primary_image_url"),
        "image_urls":        p.get("image_urls", []),
        "store_logo_url":    p.get("store_logo_url"),
    }
    store_obj = {
        "name":       p.get("store_name"),
        "logo_url":   p.get("store_logo_url"),
        "banner_url": p.get("store_banner_url"),
    }

    event_payload = {
        "name":              p.get("name"),
        "price":             p.get("price"),
        "brand":             p.get("brand"),
        "category":          p.get("category"),
        "description":       enriched_desc,
        "reviews_positive":  reviews,
        # Kampanya bilgileri
        "discount_pct":      discount_pct if discount_pct else None,
        "discount_percent":  discount_pct if discount_pct else None,
        "campaign_price":    campaign_price,
        "campaign_start":    campaign_start,
        "campaign_end":      campaign_end,
        # Ek context
        "kar":               extra_ctx.get("kar"),
        "marj_yuzde":        extra_ctx.get("marj_yuzde"),
        "stok":              extra_ctx.get("stok"),
        "rating":            extra_ctx.get("rating"),
        "rating_count":      extra_ctx.get("rating_count"),
        "bu_ay_satis":       extra_ctx.get("bu_ay_satis"),
        # FLAT alanlar — content_generator_node primary_image_url /
        # image_url / image_urls / store_logo_url / logo_url /
        # banner_url / store_banner_url isimleriyle okuyor.
        "primary_image_url": p.get("primary_image_url"),
        "image_url":         p.get("image_url"),
        "image_urls":        p.get("image_urls", []),
        "store_name":        p.get("store_name"),
        "store_logo_url":    p.get("store_logo_url"),
        "logo_url":          p.get("store_logo_url"),
        "banner_url":        p.get("store_banner_url"),
        "store_banner_url":  p.get("store_banner_url"),
        # NESTED — outer item / store yoksa buradan okuyor
        "item":              item_obj,
        "store":             store_obj,
        "source":            "business_chat_one_shot",
    }

    try:
        rule = parse_rule(question, user_id=user_id)
    except Exception as exc:
        print(f"[CHAT] parse_rule failed: {exc}")
        return None

    # event_type prefilter+LLM ile bulunamadıysa story.created default'una çek
    if "trigger.event_type" in (rule.missing_fields or []):
        try:
            rule.trigger = TriggerSpec(event_type="story.created")
            rule.missing_fields = [
                m for m in rule.missing_fields if m != "trigger.event_type"
            ]
        except Exception:
            pass

    rule.enabled = True
    try:
        saved = save_rule(rule)
    except Exception as exc:
        print(f"[CHAT] save_rule failed: {exc}")
        return None

    event = {
        "event_id":     None,
        "event_type":   saved.trigger.event_type,
        "payload":      event_payload,
        "subject_type": "Product",
        "subject_id":   (product or {}).get("id"),
        "received_at":  None,
        # OUTER seviyede item + store — Path A (listener) ile parite.
        # content_generator_node ilk olarak event.item / event.store'a
        # bakıyor; oraya da bilgi koymak referans-görsel yolunu garanti
        # ediyor.
        "item":         item_obj,
        "store":        store_obj,
    }
    exec_result: dict
    try:
        exec_result = start_execution(rule=saved, event=event, user_id=user_id)
    except Exception as exc:
        print(f"[CHAT] start_execution failed: {exc}")
        exec_result = {"status": "failed", "error": str(exc)[:200]}

    # One-shot garantisi: kural yeni eventle TEKRAR tetiklenmesin.
    # Resume yolları (waiting_human / waiting_timer) id ile çalışır,
    # enabled flag'i kontrol etmez — yani aktif execution etkilenmez.
    try:
        if saved.id:
            delete_rule(int(saved.id))
    except Exception as exc:
        print(f"[CHAT] post-execution cleanup failed: {exc}")

    status = exec_result.get("status") or "unknown"
    product_name = (product or {}).get("name") or "ürün"
    channel = (saved.content.channel if saved.content else None) or "instagram"
    try:
        from nl_rule_parser import _channel_label, _humanize_seconds
        channel_label = _channel_label(channel)
        delay = int(saved.timing.delay_seconds) if saved.timing else 0
        delay_text = _humanize_seconds(delay)
    except Exception:
        channel_label = channel.title()
        delay = int(saved.timing.delay_seconds) if saved.timing else 0
        delay_text = f"{max(1, delay // 86400)} gün"

    if status == "waiting_human":
        summary = (
            f"✓ {product_name} için {channel_label} paylaşımı planlandı. "
            "Onay bekleyenler sayfasına düştü, oradan onaylayabilirsin."
        )
    elif status == "waiting_timer":
        summary = (
            f"✓ {product_name} için {delay_text} sonrasına planlandı. "
            "Zamanı geldiğinde onay bekleyenlere düşecek."
        )
    elif status == "completed":
        summary = f"✓ {product_name} için {channel_label} paylaşımı tamamlandı."
    elif status == "failed":
        err = exec_result.get("error") or "akış başlatılamadı"
        summary = f"⚠ {product_name} için akış başlatıldı ama hata: {err}"
    else:
        summary = f"✓ {product_name} için akış {status} durumunda."

    return {
        "status":        status,
        "summary":       summary,
        "rule_id":       saved.id,
        "execution_id":  exec_result.get("execution_id"),
        "approval_id":   exec_result.get("approval_id"),
        "channel":       channel,
        "channel_label": channel_label,
        "product":       product,
    }


def _compose_context_text(pg_ctx: dict) -> str:
    """LLM'e geçecek context_text'i pg_context'ten üret.

    Üç dal: (1) gerçek SQL hatası → "icat etme" uyarısı, (2) başarılı veri →
    veri bloğu, (3) başarılı ama 0 satır → "kayıt yok". Hata ve boş-sonuç
    aynı mesajla birleşmez.
    """
    if not pg_ctx or pg_ctx.get("type") != "smart_context":
        return ""

    if pg_ctx.get("is_error") or pg_ctx.get("error"):
        return (
            "(Bu soruyu işlerken teknik bir SQL hatası oluştu. "
            "Kullanıcıya 'bu soruyu işlerken teknik bir sorun oluştu, "
            "tekrar dener misin' gibi nazik bir cevap ver. "
            "SAYI veya VERİ İCAT ETME. 'Kayıt yok' DEME — bu farklı bir durum.)"
        )

    raw_text = pg_ctx.get("text") or ""
    row_count = pg_ctx.get("row_count", 0)
    if raw_text and row_count > 0:
        desc = pg_ctx.get("description") or ""
        return (
            f"DB VERİSİ ({desc}):\n"
            f"{raw_text}\n"
            f"(Toplam {row_count} kayıt — bu sayıları ve değerleri aynen kullan)"
        )
    if row_count == 0:
        return "(Bu sorgu için veritabanında kayıt bulunamadı.)"
    return ""


def answer_question(
    question: str,
    user_id: int = 1,
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Answer a chat question with real retrieved data + conversation memory.

    Args:
        question:    raw user input.
        user_id:     tenant binding.
        session_id:  client-supplied session token (dashboard localStorage).
                     If omitted, a new session is opened and returned to the
                     caller for follow-up turns.

    Returns: see body — the canonical chat response shape.
    """
    question = (question or "").strip()
    if not question:
        return {
            "question": "",
            "intent": "empty",
            "answer": "Sormak istediğin bir konu yazabilirsin — satış, stok, "
                      "kampanya, müşteri yorumları, iş akışları, kargo...",
            "data": {},
            "recommendations": [],
            "confidence": 0.0,
            "sources": [],
            "stages": [],
            "session_id": session_id,
            "mode": "noop",
            "is_followup": False,
        }

    # ----- Open / load session -----
    session = memory.open_session(user_id=user_id, session_id=session_id)
    sid = session["id"]

    # ----- Action vs Query early dispatch -----
    # Önceki conversational_rule_edit substring-tabanlı early-dispatch'i
    # normal soruları yanlışlıkla kural mutasyonuna dönüştürüyordu. Onun
    # yerine ucuz bir LLM sınıflandırıcı: "action" ise nl_rule_parser +
    # LangGraph one-shot akışı; "query" ise mevcut retrieval+synthesizer.
    intent_class = _classify_intent(question)
    if intent_class == "action":
        one_shot = _one_shot_run(question, user_id)
        if one_shot:
            product = one_shot.get("product") or {}
            entity_id = _coerce_entity_id(product.get("id")) if product.get("id") else None
            channel = one_shot.get("channel") or "generic"
            routed = f"action:{channel}"

            memory.record_turn(
                session_id=sid, user_id=user_id,
                question=question, resolved_question=None,
                intent="one_shot_action",
                routed_intent=routed,
                primary_entity_type="product" if product.get("id") else None,
                primary_entity_id=entity_id,
                primary_entity_label=product.get("name"),
                answer=one_shot["summary"],
                confidence=0.9,
            )
            return {
                "question": question,
                "resolved_question": None,
                "is_followup": False,
                "follow_up_rationale": "",
                "session_id": sid,
                "intent": "one_shot_action",
                "routed_intent": routed,
                "active_entity": product.get("name"),
                "answer": one_shot["summary"],
                "stages": [
                    "Niyet: aksiyon komutu olarak sınıflandırıldı.",
                    f"Kural #{one_shot.get('rule_id')} oluşturuldu, akış başlatıldı.",
                    f"Durum: {one_shot.get('status')}.",
                ],
                "data": {
                    "one_shot": {
                        "rule_id":      one_shot.get("rule_id"),
                        "execution_id": one_shot.get("execution_id"),
                        "approval_id":  one_shot.get("approval_id"),
                        "status":       one_shot.get("status"),
                        "channel":      one_shot.get("channel"),
                        "product":      product,
                    },
                },
                "recommendations": [],
                "confidence": 0.9,
                "mode": "one_shot_action",
                "model": None,
                "latency_ms": 0,
                "sources": ["nl_rule_parser", "langgraph_engine"],
                "fallback": False,
                "anti_repetition_active": False,
            }
        # one_shot None döndüyse (heavy import yok, parse/save fail) sessizce
        # query yoluna düş — kullanıcı en azından bilgi cevabı alır.
        print("[CHAT] one_shot returned None, falling back to query path")
        # ----- Veri gerektirmeyen sorular: retrieval'sız sohbet/advisory -----
        # ----- Veri gerektirmeyen sorular: retrieval'sız sohbet/advisory -----
    if intent_class in ("chat", "advisory"):
        ctx = _light_state_summary(user_id) if intent_class == "advisory" else ""
        return _answer_chat(
            question, user_id, sid, _resolve_api_key(user_id),
            context_data=ctx, advisory=(intent_class == "advisory"),
        )

    # ----- Coreference: önceki turdan aktif entity'yi al -----
    # bchat_turns.primary_entity_label'dan son non-null kaydı okur. "Bu ürün"
    # gibi pronoun varsa route() bunu kullanıp soruyu rewrite eder.
    prev_ctx = memory.conversation_context(sid)
    prev_active_label = prev_ctx.get("active_entity_label")
    prev_active_id = prev_ctx.get("active_entity_id")
    prev_active_type = prev_ctx.get("active_entity_type")

    # ----- Smart retrieval (yeni akış) -----
    # business_query_router → mention parser → access → cache → intent →
    # smart_query → format. Asla _pg_full_snapshot çekmez.
    retrieval = query_router.route(
        question,
        user_id=user_id,
        session_id=sid,
        active_entity_label=prev_active_label or "",
        active_entity_id=prev_active_id,
        active_entity_type=prev_active_type,
    )
    if retrieval is None:
        retrieval = {
            "intent": "general_overview",
            "routed_intent": "smart_query",
            "answer": "",
            "data": {"pg_context": {}, "op_context": {}},
            "recommendations": [],
            "confidence": 0.5,
        }

    retrieval_data = retrieval.get("data") or {}

    # Rate limit short-circuit — LLM çağrısı yapma, hazır cevabı dön
    if retrieval_data.get("rate_limited"):
        return {
            "question": question,
            "resolved_question": None,
            "is_followup": False,
            "follow_up_rationale": "rate_limited",
            "session_id": sid,
            "intent": "rate_limited",
            "routed_intent": "rate_limited",
            "active_entity": None,
            "answer": retrieval.get("answer", "Çok fazla soru gönderiyorsun, biraz bekle."),
            "stages": ["Rate limit: dakikalık sınır aşıldı."],
            "data": retrieval_data,
            "recommendations": [],
            "confidence": 1.0,
            "mode": "rate_limited",
            "model": None,
            "latency_ms": 0,
            "sources": ["rate_limiter"],
            "fallback": False,
            "anti_repetition_active": False,
        }

    # ----- API key + model resolution -----
    api_key = _resolve_api_key(user_id)
    model = retrieval_data.get("model_override") or ai_synthesizer.CHAT_LLM_MODEL

    # ----- Long-term memory + son session özetleri system prompt'a eklenir -----
    user_memories = memory.get_user_memories(user_id)
    past_summaries = memory.get_session_summary(user_id, limit=2)

    full_system_prompt = ai_synthesizer._SYSTEM_PROMPT
    if user_memories:
        full_system_prompt += f"\n\nKullanıcı hakkında bilinen notlar:\n{user_memories}"
    if past_summaries:
        full_system_prompt += f"\n\nÖnceki konuşmalardan:\n{past_summaries}"

    pg_ctx = retrieval_data.get("pg_context") or {}
    context_text = _compose_context_text(pg_ctx)
    row_count = pg_ctx.get("row_count", 0)
    is_sql_error = bool(pg_ctx.get("is_error") or pg_ctx.get("error"))

    _mode_label = "MOD_1_SOHBET"
    if context_text and row_count > 0 and not is_sql_error:
        _mode_label = "MOD_2_VERI" if row_count <= 10 else "MOD_3_KARMA"

    # ----- OpenAI native chat history -----
    messages = memory.build_openai_messages(
        session_id=sid,
        system_prompt=full_system_prompt,
        new_question=question,
        context_data=context_text,
        limit_turns=10,
    )

    # ----- LLM çağrısı -----
    import time as _time
    _t0 = _time.monotonic()
    try:
        answer_text, model_id, tokens = ai_synthesizer.synthesize_with_openai(
            messages=messages,
            model=model,
            api_key=api_key,
        )
        if not answer_text:
            raise RuntimeError("empty completion")
        synth_mode = "llm"
        synth_error = None
    except Exception as exc:
        print(f"[CHAT] synth failed: {exc}")
        answer_text = "Şu an cevap üretemedim, biraz sonra tekrar dener misin?"
        model_id = None
        tokens = 0
        synth_mode = "deterministic_fallback"
        synth_error = str(exc)[:200]
    latency_ms = int((_time.monotonic() - _t0) * 1000)

    cost = _estimate_cost(tokens, model_id or model)

    routed_intent = retrieval.get("routed_intent") or retrieval.get("intent")

    # ----- Bu tur için aktif entity'yi çöz -----
    # Öncelik (sıralama önemli, eski sırayla desync oluyordu):
    #   (1) route() pronoun rewrite ettiyse → önceki aktif entity'yi devral
    #       (route()'un resolved_entity_* alanı atomik kaynak — desync yok).
    #   (2) Rewrite olmadıysa _lookup_basic_product DENE; sadece STRONG match
    #       (score>=2 AND ratio>=0.5) kabul edilir.
    #   (3) İkisi de yoksa None — sonraki tur için zincir kırılır.
    current_entity_label: str | None = None
    current_entity_id: str | None = None
    current_entity_type: str | None = None

    if retrieval_data.get("pronoun_rewritten"):
        current_entity_label = retrieval_data.get("resolved_entity_label")
        current_entity_id = retrieval_data.get("resolved_entity_id")
        current_entity_type = retrieval_data.get("resolved_entity_type")
    else:
        found_in_this_turn = product_resolver.resolve_single(question, user_id, api_key=api_key)
        if found_in_this_turn:
            current_entity_label = found_in_this_turn.get("name")
            current_entity_id = found_in_this_turn.get("id")
            current_entity_type = "product"

    # ----- Tur kaydı -----
    memory.record_turn(
        session_id=sid,
        user_id=user_id,
        question=question,
        answer=answer_text,
        intent=retrieval.get("intent"),
        model_used=model_id,
        tokens_used=tokens,
        cost_usd=cost,
        primary_entity_type=current_entity_type,
        primary_entity_id=current_entity_id,
        primary_entity_label=current_entity_label,
    )

    # ----- Long-term memory extract (best effort) -----
    try:
        memory.extract_and_save_memories(sid, user_id, question, answer_text, api_key)
    except Exception as exc:
        print(f"[CHAT] memory extract failed: {exc}")

    # ----- 20 tur dolduysa session özetle (best effort) -----
    try:
        if len(memory.recent_turns(sid, limit=21)) >= 20:
            memory.summarize_session(sid, api_key=api_key)
    except Exception as exc:
        print(f"[CHAT] session summarize failed: {exc}")

    sources = ["smart_query"]
    if synth_mode == "llm":
        sources.append("openai_native_history")
    else:
        sources.append("deterministic_fallback")

    return {
        "question": question,
        "resolved_question": retrieval_data.get("effective_question"),
        "is_followup": bool(retrieval_data.get("pronoun_rewritten")),
        "follow_up_rationale": (
            "pronoun_rewritten_via_active_entity"
            if retrieval_data.get("pronoun_rewritten") else "native_history"
        ),
        "session_id": sid,
        "intent": retrieval.get("intent"),
        "routed_intent": routed_intent,
        "active_entity": current_entity_label,
        "answer": answer_text,
        "stages": [
            f"Niyet: {retrieval.get('intent')}",
            f"Veri satırı: {(retrieval_data.get('pg_context') or {}).get('row_count', 0)}",
            f"Model: {model_id or model}",
        ],
        "data": retrieval_data,
        "recommendations": retrieval.get("recommendations", []),
        "confidence": retrieval.get("confidence", 0.9),
        "mode": synth_mode,
        "model": model_id,
        "latency_ms": latency_ms,
        "tokens_used": tokens,
        "cost_usd": cost,
        "sources": sources,
        "fallback": synth_mode != "llm",
        "anti_repetition_active": False,
        "error": synth_error,
    }


def _resolve_api_key(user_id: int) -> str | None:
    """Per-user OpenAI key resolution.

    Şu an: sadece env var OPENAI_API_KEY.
    TODO: app_settings tablosu eklendiğinde user_id ile DB lookup ekle.
    """
    return os.environ.get("OPENAI_API_KEY")


# OpenAI fiyatları (yaklaşık, total_tokens üzerinden ortalama input/output).
# Kesin değil ama kabaca maliyet takibine yeter.
_COST_RATES = {
    "gpt-4o-mini": 0.000_000_3,   # ~$0.30 per 1M tokens (input+output karması)
    "gpt-4o": 0.000_005,           # ~$5 per 1M tokens
}


def _estimate_cost(tokens: int, model: str | None) -> float:
    if not tokens or not model:
        return 0.0
    rate = _COST_RATES.get(model)
    if rate is None:
        # gpt-4o-mini-2024-... gibi versiyonlu isimleri yakala
        for k, v in _COST_RATES.items():
            if model.startswith(k):
                rate = v
                break
    rate = rate or 0.000_000_3
    return round(int(tokens) * rate, 6)


def supported_query_intents() -> list[str]:
    """Exposed to /api/internal/chat/intents for the dashboard help tooltip."""
    return query_router.list_supported_intents()


def _resolve_api_key(user_id: int) -> str | None:
    """Per-user OpenAI key resolution.
    Şu an: sadece env var. TODO: app_settings DB lookup.
    """
    return os.environ.get("OPENAI_API_KEY")


_COST_RATES = {
    "gpt-4o-mini": 0.000_000_3,
    "gpt-4o": 0.000_005,
}


def _estimate_cost(tokens: int, model: str | None) -> float:
    if not tokens or not model:
        return 0.0
    rate = _COST_RATES.get(model)
    if rate is None:
        for k, v in _COST_RATES.items():
            if model.startswith(k):
                rate = v
                break
    rate = rate or 0.000_000_3
    return round(int(tokens) * rate, 6)