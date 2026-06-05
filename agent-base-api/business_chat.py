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


def _classify_action_or_query(question: str) -> str:
    """Ucuz LLM sınıflandırıcı — 'action' veya 'query' döner.

    Sosyal medya / kampanya / banner / story / post / paylaşım gibi YAPILMASI
    istenen bir komut mu, yoksa veriden cevap isteyen bir bilgi sorusu mu?
    Fail durumunda (key yok, network, parse) 'query' döner — güvenli taraf,
    sistemde mutasyon olmaz.
    """
    if not ai_synthesizer.CHAT_USE_LLM:
        return "query"
    if not os.environ.get("OPENAI_API_KEY"):
        return "query"
    try:
        from openai import OpenAI
        client = OpenAI(timeout=8)
        completion = client.chat.completions.create(
            model=ai_synthesizer.CHAT_LLM_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Aşağıdaki Türkçe mesaj bir e-ticaret operatöründen "
                        "geliyor. Mesaj iki sınıftan biridir:\n"
                        "  - action: sosyal medya / kampanya / banner / story / "
                        "post / paylaşım / kupon gibi YAPILMASI istenen bir "
                        "aksiyon.\n"
                        "  - query: mevcut veriden bilgi soran soru.\n\n"
                        "SADECE 'action' veya 'query' yaz. Başka hiçbir şey yazma."
                    ),
                },
                {"role": "user", "content": question},
            ],
            temperature=0,
            max_tokens=10,
        )
        raw = (completion.choices[0].message.content or "").strip().lower()
        return "action" if "action" in raw else "query"
    except Exception as exc:
        print(f"[CHAT] action/query classify failed: {exc}")
        return "query"


def _one_shot_find_product(user_id: int, question: str) -> dict | None:
    """Soruda geçen ürünü PG'de fuzzy ara; ilk eşleşmeyi zenginleştirilmiş
    dict olarak döner.

    Zenginleştirme:
        - Product.images relationship → image_urls + thumb_url
          (business_query_router._pg_load_full ile aynı kanonik yol)
        - Bağlı Store → name + logo_url + banner_url
          (mağaza logosu / banner content_generator_node'da fallback ref)

    Bu alanlar eksikse pipeline reference-image alamaz ve pure text-to-image
    fallback'ine düşer; sonuç markayla alakasız görsel olur (örn. Razer için
    portakal suyu).
    """
    # PG'de ilike + Türkçe synonym tabanlı ürün araması — eskiden
    # business_query_router._pg_search_products vardı, refactor'da kaldırıldı.
    # Burada inline tutuyoruz, böylece chat one-shot başka modülün iç API'sine
    # bağımlı kalmıyor.
    text = (question or "").strip().lower()
    if not text:
        return None

    SYNONYMS = {
        "fare":     ["mouse", "fare"],
        "klavye":   ["keyboard", "klavye"],
        "kulaklık": ["headset", "headphone", "kulaklık"],
        "mouse":    ["mouse", "fare"],
        "keyboard": ["keyboard", "klavye"],
        "ekran":    ["monitor", "display", "ekran"],
        "kamera":   ["camera", "webcam", "kamera"],
        "mikrofon": ["microphone", "mikrofon"],
    }
    raw_words = [w for w in text.replace("-", " ").split() if len(w) > 1]
    search_terms: list[str] = []
    for w in raw_words:
        search_terms.extend(SYNONYMS.get(w, [w]))
    search_terms = list(dict.fromkeys(search_terms))
    if not search_terms:
        return None

    try:
        from app.core.database import SessionLocal
        from app.models.product import Product
        from app.models.store import Store
        from app.models.product_image import ProductImage  # noqa: F401  (mapper bootstrap)
        from sqlalchemy import or_, select

        with SessionLocal() as session:
            conditions = [
                or_(
                    Product.name.ilike(f"%{t}%"),
                    Product.brand.ilike(f"%{t}%"),
                    Product.category.ilike(f"%{t}%"),
                    Product.description.ilike(f"%{t}%"),
                )
                for t in search_terms
            ]
            candidates = list(session.scalars(
                select(Product)
                .join(Store, Product.store_id == Store.id)
                .where(Store.user_id == int(user_id))
                .where(or_(*conditions))
            ).all())
            # Session-içinde column'ları okuyalım, dışarı dict olarak çıkalım
            if not candidates:
                return None
            cand = candidates[0]
            first = {
                "id":          str(cand.id),
                "name":        cand.name,
                "brand":       getattr(cand, "brand", None),
                "category":    getattr(cand, "category", None),
                "price":       float(cand.price) if cand.price is not None else None,
                "description": getattr(cand, "description", None),
                "store_id":    getattr(cand, "store_id", None),
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
        from structured_rule_engine import save_rule, set_enabled
        from langgraph_engine.runtime import start_execution
        from structured_rule import TriggerSpec
    except Exception as exc:
        print(f"[CHAT] one-shot heavy imports failed: {exc}")
        return None

    product = _one_shot_find_product(user_id, question)
    reviews = _one_shot_positive_reviews(product["id"], limit=5) if product else []
    p = product or {}

    # Açıklamayı yorumlarla zenginleştir — caption üreticisi description
    # alanını okuyor, olumlu yorumlar caption'ı daha somut yapıyor.
    base_desc = p.get("description") or ""
    if reviews:
        review_text = "\n".join(f"- {r}" for r in reviews[:5])
        enriched_desc = (
            f"{base_desc}\n\nMüşteri yorumları:\n{review_text}"
            if base_desc else f"Müşteri yorumları:\n{review_text}"
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
            set_enabled(int(saved.id), False)
    except Exception as exc:
        print(f"[CHAT] post-execution disable failed: {exc}")

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
    intent_class = _classify_action_or_query(question)
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

    # ----- Resolve follow-up reference -----
    resolution = memory.resolve_follow_up(question, sid)
    resolved_q = resolution.resolved_question

    # ----- Retrieval -----
    # active_entity_label, follow-up çözümlemesinden geliyor — entity-aware
    # retriever'lar (örn. product_reviews_lookup) sorunun içinde ürün adı
    # geçmediği durumda bu label'a düşerek doğru ürünü bulur.
    retrieval = query_router.route(
        resolved_q,
        user_id=user_id,
        active_entity_label=resolution.inherited_entity_label,
    )
    used_fallback = retrieval is None
    if used_fallback:
        retrieval = _build_open_ended_retrieval(resolved_q, user_id)

    # ----- Memory context for the LLM -----
    mem_ctx = memory.conversation_context(sid)

    # ----- Synthesize natural prose -----
    synth = ai_synthesizer.synthesize(
        question=question,
        resolved_question=resolved_q,
        retrieval=retrieval,
        memory_ctx=mem_ctx,
        is_followup=resolution.is_followup,
        inherited_label=resolution.inherited_entity_label,
    )

    answer_text = synth.answer

    # ----- Record turn -----
    entity_type, entity_id, entity_label = _entity_from_retrieval(retrieval)
    # If retrieval didn't bring its own entity but we inherited one, keep that.
    if not entity_label and resolution.inherited_entity_label:
        entity_label = resolution.inherited_entity_label

    routed_intent = retrieval.get("routed_intent") or retrieval.get("intent")

    memory.record_turn(
        session_id=sid,
        user_id=user_id,
        question=question,
        resolved_question=resolved_q if resolution.is_followup else None,
        intent=routed_intent if not used_fallback else "open_ended",
        routed_intent=routed_intent,
        primary_entity_type=entity_type,
        primary_entity_id=entity_id,
        primary_entity_label=entity_label,
        answer=answer_text,
        confidence=retrieval.get("confidence"),
    )

    sources: list[str] = ["retrieval"] if not used_fallback else [
        "business_state", "business_intelligence", "cross_event_reasoning",
    ]
    if synth.mode == "llm":
        sources.append("ai_synthesizer")
    else:
        sources.append("deterministic_fallback")

    return {
        "question": question,
        "resolved_question": resolved_q if resolution.is_followup else None,
        "is_followup": resolution.is_followup,
        "follow_up_rationale": resolution.rationale,
        "session_id": sid,
        "intent": routed_intent if not used_fallback else "open_ended",
        "routed_intent": retrieval.get("routed_intent"),
        "active_entity": entity_label,
        "answer": answer_text,
        "stages": synth.stages,
        "data": retrieval.get("data", {}),
        "recommendations": retrieval.get("recommendations", []),
        "confidence": retrieval.get("confidence", 0.6),
        "mode": synth.mode,
        "model": synth.model,
        "latency_ms": synth.latency_ms,
        "sources": sources,
        "fallback": used_fallback,
        "anti_repetition_active": bool(mem_ctx.get("anti_phrases")),
    }


def supported_query_intents() -> list[str]:
    """Exposed to /api/internal/chat/intents for the dashboard help tooltip."""
    return query_router.list_supported_intents()
