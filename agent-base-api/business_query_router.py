"""
Business query router — saf veri çekici.

Karar verme YOK: keyword listesi, Türkçe stem matching, intent detection,
ürün skorlaması, "soru hangi türden?" branch'i — hiçbiri yok. Tek görev:

    PG'den o kullanıcının TÜM mağaza + ürün + yorum + SSS verisini al,
    ham yapıda tek bir payload olarak ai_synthesizer'a teslim et.

Hangi parçanın soruyla ilgili olduğunu LLM seçer; biz seçmeyiz. Kullanıcı
"magza", "urun", "magzalarımdaki ürün listesini ver" — ne yazarsa yazsın
Python tarafında dallanma yok, her zaman aynı tam snapshot gider.

Kısa süreli hafıza (son N tur) business_chat → memory.conversation_context
üzerinden synthesizer'a ayrı kanaldan gider; bu modül onunla ilgilenmez.
"""

from __future__ import annotations

from typing import Optional


# SQLAlchemy mapper'ların eksiksiz initialization'ı için tüm bağımlı modelleri
# bir kez registry'ye yükle (string-based relationship eval zamanı gerekli).
try:
    from app.models.store import Store
    from app.models.product import Product
    from app.models.product_image import ProductImage  # noqa: F401
    from app.models.product_review import ProductReview  # noqa: F401
    from app.models.product_faq import ProductFaq  # noqa: F401
    from app.models.product_metrics_weekly import ProductMetricsWeekly  # noqa: F401
except Exception as _model_bootstrap_exc:
    print(f"[ROUTER] model bootstrap import failed: {_model_bootstrap_exc}")


# Soru hakkında karar değil — sadece LLM context-window'unu güvende tutmak
# için ürün başına ham yorum/SSS üst sınırı. Soruya göre değişmez; her zaman
# en yeni N kayıt iletilir.
_REVIEWS_PER_PRODUCT_CAP = 25
_FAQS_PER_PRODUCT_CAP = 15


def _pg_full_snapshot(user_id: int) -> dict:
    """Kullanıcıya ait tüm mağaza + ürün + yorum + SSS'yi tek payload'da döner.

    Karar yok, filtreleme yok: ne PG'de varsa o gelir. Yorum/SSS sayısı
    sabit bir üst sınırla kesilir (sadece bağlam boyutu güvenliği — soruya
    bağlı bir tercih değil).
    """
    try:
        from app.core.database import SessionLocal
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        with SessionLocal() as session:
            store_rows = list(session.scalars(
                select(Store)
                .where(Store.user_id == int(user_id))
                .order_by(Store.id.desc())
            ).all())

            stores = [{
                "id":         str(s.id),
                "name":       s.name,
                "rating":     float(s.rating) if s.rating is not None else None,
                "logo_url":   getattr(s, "logo_url", None),
                "banner_url": getattr(s, "banner_url", None),
                "status":     getattr(s, "status", None),
            } for s in store_rows]

            product_rows = list(session.scalars(
                select(Product)
                .join(Store, Product.store_id == Store.id)
                .where(Store.user_id == int(user_id))
                .order_by(Product.created_at.desc())
                .options(
                    selectinload(Product.reviews),
                    selectinload(Product.faqs),
                    selectinload(Product.images),
                )
            ).all())

            products: list[dict] = []
            for p in product_rows:
                reviews_sorted = sorted(
                    list(p.reviews or []),
                    key=lambda r: (r.review_date or "", getattr(r, "id", 0)),
                    reverse=True,
                )[:_REVIEWS_PER_PRODUCT_CAP]
                faqs_capped = list(p.faqs or [])[:_FAQS_PER_PRODUCT_CAP]

                products.append({
                    "id":           str(p.id),
                    "store_id":     str(p.store_id) if p.store_id is not None else None,
                    "name":         p.name,
                    "brand":        p.brand,
                    "category":     p.category,
                    "price":        float(p.price)    if p.price    is not None else None,
                    "discount":     float(p.discount) if p.discount is not None else None,
                    "stock":        p.stock,
                    "rating":       float(p.rating)   if p.rating   is not None else None,
                    "rating_count": p.rating_count,
                    "status":       p.status,
                    "weekly_sales": p.weekly_sales,
                    "description":  p.description,
                    "thumb_url":    (p.images[0].url if p.images else None),
                    "reviews": [{
                        "rating":      int(r.rating) if r.rating is not None else None,
                        "content":     r.content,
                        "review_date": r.review_date,
                    } for r in reviews_sorted],
                    "faqs": [{
                        "question": f.question,
                        "answer":   f.answer,
                    } for f in faqs_capped],
                })

            return {
                "type":          "full_context",
                "stores":        stores,
                "store_count":   len(stores),
                "products":      products,
                "product_count": len(products),
            }
    except Exception as exc:
        print(f"[ROUTER] _pg_full_snapshot error: {exc}")
        return {
            "type":          "full_context",
            "stores":        [],
            "store_count":   0,
            "products":      [],
            "product_count": 0,
        }


def route(
    question: str,
    *,
    user_id: int = 1,
    active_entity_label: str = "",
    **extra,
) -> Optional[dict]:
    """Soruya bakmadan tüm PG snapshot'ı çek; ai_synthesizer'a teslim et.

    `active_entity_label` ve diğer extra'lar artık kullanılmıyor — imza
    business_chat ile uyumlu kalsın diye duruyor. Karar LLM'e bırakıldı.
    """
    question = (question or "").strip()
    if not question:
        return None

    pg_ctx = _pg_full_snapshot(user_id)

    return {
        "intent":        "smart_query",
        "routed_intent": "smart_query",
        "answer":        "",
        "data": {
            "pg_context": pg_ctx,
            "op_context": {},
        },
        "recommendations": [],
        "confidence":      0.9,
    }


def list_supported_intents() -> list[str]:
    return ["smart_query"]
