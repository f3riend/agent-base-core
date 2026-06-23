"""
product_resolver.py — LLM tabanlı ürün çözümleme.

business_chat.py'deki keyword/stopword/synonym + skor heuristiğinin
(`_extract_product_search_terms`, `_PRODUCT_SEARCH_STOPWORDS`,
`_PRODUCT_SEARCH_SYNONYMS`, `_is_strong_match`) yerine geçer.

Kullanıcının gerçek kataloğunu (id + name + brand) LLM'e verir, mesajda
kastedilen ürün(ler)i semantik olarak eşleştirir. Tek kelimelik marka adları
("Pantene", "Razer"), Türkçe ekler ("Pantene'ye"), yazım hataları sorunsuz
çözülür — stop-word listesi YOK, regex YOK.

İki giriş noktası:
  - resolve_products(...)      → light: [{"id","name","brand"}], coreference
                                 çapası ve action ürün seçimi için.
  - resolve_and_enrich(...)    → full: _one_shot_find_product ile aynı şekil
                                 (image_urls + store_* dahil), drop-in.

Ölçekleme notu: katalog çok büyüdüğünde (10k+ ürün) catalog_text token
maliyeti artar — o noktada burada bir embedding/vektör prefilter eklenir.
Şimdilik ilk `max_catalog` ürün gönderilir.
"""
from __future__ import annotations

import json
import os
from typing import Optional


_RESOLVE_PROMPT = (
    "Bir e-ticaret asistanısın. Sana kullanıcının ürün kataloğu (her satır: "
    "id | ad | marka) ve bir mesaj verilecek. Mesajda kastedilen ürün(ler)i "
    "SADECE KATALOGDAN seç, id'lerini döndür.\n"
    "Eşleştirme kuralları:\n"
    "- KISMİ eşleşme yeterlidir: mesajdaki marka veya ürün adının bir parçası "
    "katalogdaki bir ürünle örtüşüyorsa onu seç. Tam ad gerekmez. Örn. katalogda "
    "'Pantene Pro-V ... Saç Bakım Yağı' varsa, mesajdaki 'Pantene' bununla eşleşir.\n"
    "- Türkçe ekleri yok say: \"Pantene'ye\", \"Pantene'nin\", 'Pantene'yi' hepsi "
    "'Pantene' demektir.\n"
    "- Mesaj o ürün hakkında BİLGİ soruyorsa da (fiyat, stok, yorum, puan, kâr, "
    "marj) ve ürün katalogda varsa yine onu seç. Örn. 'Pantene fiyatı ne' → Pantene.\n"
    "- Sadece şu durumlarda BOŞ liste döndür: (a) genel soru ('kaç ürünüm var'), "
    "(b) kriter-bazlı seçim ('en çok satan 2 ürün', 'en kârlı ürün'), (c) hiçbir "
    "ürün adı/markası geçmeyip yalnızca zamir olan mesaj ('bu ürün', 'şu', 'o').\n"
    "- Id UYDURMA, yalnızca katalogdaki id'leri kullan.\n"
    "- Birden fazla ürün açıkça geçiyorsa hepsini döndür.\n"
    "SADECE şu JSON ile yanıt ver, başka hiçbir şey yazma:\n"
    '{"product_ids": ["<id>", ...]}'
)


def _fetch_catalog(user_id: int, limit: int, query: str | None = None) -> list[dict]:
    """Aday ürünleri çek (id, name, brand).

    query verilirse: ÖNCE ChromaDB'den soruya en yakın `limit` ürünü al — token
    katalog büyüklüğünden bağımsız sabit kalır, 300+ ürünlü mağazada da çalışır.
    ChromaDB boş/erişilemez ya da query yoksa: SQL ile tüm katalog (fallback,
    bugünkü davranış — küçük mağaza veya index henüz kurulmamışsa)."""
    # 1) Vektör arama (ölçeklenen yol)
    if query:
        try:
            from app.services import product_index
            hits = product_index.search(query, user_id=int(user_id), n=limit)
            if hits:
                # brand metadata'da yok; isim eşleştirme için name yeterli, brand boş geçilir
                return [
                    {"id": str(h["product_id"]), "name": h.get("name") or "", "brand": ""}
                    for h in hits if h.get("product_id")
                ]
        except Exception as exc:
            print(f"[PRODUCT_RESOLVER] chroma search fallback: {exc}")

    # 2) Fallback: SQL ile tüm katalog (küçük mağaza / index yok)
    from app.core.database import SessionLocal
    from app.models.product import Product
    from app.models.store import Store
    from app.models.product_image import ProductImage  # noqa: F401
    from app.models.product_review import ProductReview  # noqa: F401
    from app.models.product_faq import ProductFaq  # noqa: F401
    from app.models.product_metrics_weekly import ProductMetricsWeekly  # noqa: F401
    from sqlalchemy import select

    with SessionLocal() as s:
        rows = s.execute(
            select(Product.id, Product.name, Product.brand)
            .join(Store, Product.store_id == Store.id)
            .where(Store.user_id == int(user_id))
            .where(Product.is_active == True)  # noqa: E712
            .limit(limit)
        ).all()
    return [
        {"id": str(r.id), "name": r.name or "", "brand": r.brand or ""}
        for r in rows
    ]

def resolve_products(
    question: str,
    user_id: int,
    *,
    api_key: Optional[str] = None,
    max_catalog: int = 300,
) -> list[dict]:
    """Soruda kastedilen ürünleri katalogtan LLM ile eşleştir.

    Döner: [{"id","name","brand"}] — boş liste = ürün adlandırılmamış.
    Fail durumunda (key yok, network, parse) boş liste — güvenli taraf,
    çağıran taraf 'ürün bulunamadı' / kriter-bazlı seçim yoluna düşer.
    """
    q = (question or "").strip()
    key = api_key or os.environ.get("OPENAI_API_KEY")
    if not q or not key:
        return []

    catalog = _fetch_catalog(user_id, max_catalog, query=q)
    if not catalog:
        return []

    by_id = {c["id"]: c for c in catalog}
    catalog_text = "\n".join(
        f'{c["id"]} | {c["name"]} | {c["brand"]}' for c in catalog
    )

    try:
        from openai import OpenAI

        client = OpenAI(api_key=key, timeout=10)
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _RESOLVE_PROMPT},
                {
                    "role": "user",
                    "content": f"KATALOG:\n{catalog_text}\n\nMESAJ: {q}",
                },
            ],
            temperature=0,
            max_tokens=200,
            response_format={"type": "json_object"},
        )
        raw = (completion.choices[0].message.content or "").strip()
        data = json.loads(raw)
        ids = data.get("product_ids") or []
        # Sırayı koru, geçersiz id'leri ele
        seen: set[str] = set()
        out: list[dict] = []
        for i in ids:
            i = str(i)
            if i in by_id and i not in seen:
                seen.add(i)
                out.append(by_id[i])
        return out
    except Exception as exc:
        print(f"[PRODUCT_RESOLVER] resolve_products failed: {exc}")
        return []


def resolve_single(
    question: str,
    user_id: int,
    *,
    api_key: Optional[str] = None,
) -> dict | None:
    """Coreference çapası için: TAM OLARAK bir ürün eşleştiyse onu döndür.

    Sıfır eşleşme (genel soru) veya çok eşleşme (belirsiz) → None.
    Böylece aktif entity yalnızca net tek ürün varken set edilir.
    """
    matches = resolve_products(question, user_id, api_key=api_key)
    return matches[0] if len(matches) == 1 else None


def resolve_and_enrich(
    question: str,
    user_id: int,
    *,
    api_key: Optional[str] = None,
) -> dict | None:
    """Action yolu için _one_shot_find_product DROP-IN replacement.

    LLM ile tek ürünü çözer, sonra image + store bilgisiyle zenginleştirir.
    Dönen şekil _one_shot_find_product ile birebir aynı.
    """
    base = resolve_single(question, user_id, api_key=api_key)
    if not base:
        return None
    return enrich_by_id(base["id"], user_id)


def enrich_by_id(product_id: str, user_id: int) -> dict | None:
    """Verilen ürün id'sini image_urls + store_* ile zenginleştir.

    _one_shot_find_product'ın enrichment bloğuyla aynı çıktı şekli.
    Ownership güvenliği: ürün, user_id'nin bir store'una bağlı değilse None.
    """
    try:
        from app.core.database import SessionLocal
        from app.models.product import Product
        from app.models.store import Store
        from app.models.product_image import ProductImage  # noqa: F401  (mapper bootstrap)
        from app.models.product_review import ProductReview  # noqa: F401
        from app.models.product_faq import ProductFaq  # noqa: F401
        from app.models.product_metrics_weekly import ProductMetricsWeekly  # noqa: F401
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        with SessionLocal() as session:
            product = session.scalar(
                select(Product)
                .join(Store, Product.store_id == Store.id)
                .where(Product.id == product_id)
                .where(Store.user_id == int(user_id))
                .options(selectinload(Product.images))
            )
            if product is None:
                return None

            image_urls = [
                img.url for img in (product.images or [])
                if getattr(img, "url", None)
            ]
            thumb_url = image_urls[0] if image_urls else None

            store_name = store_logo_url = store_banner_url = None
            if product.store_id is not None:
                store = session.scalar(
                    select(Store).where(Store.id == product.store_id)
                )
                if store is not None:
                    store_name = getattr(store, "name", None)
                    store_logo_url = getattr(store, "logo_url", None)
                    store_banner_url = getattr(store, "banner_url", None)

            return {
                "id":                str(product.id),
                "name":              product.name,
                "brand":             getattr(product, "brand", None),
                "category":          getattr(product, "category", None),
                "price":             float(product.price) if product.price is not None else None,
                "description":       (getattr(product, "description", None) or "")[:600],
                "image_url":         thumb_url,
                "primary_image_url": thumb_url,
                "image_urls":        image_urls,
                "store_name":        store_name,
                "store_logo_url":    store_logo_url,
                "store_banner_url":  store_banner_url,
            }
    except Exception as exc:
        print(f"[PRODUCT_RESOLVER] enrich_by_id failed: {exc}")
        return None