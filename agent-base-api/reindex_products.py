#!/usr/bin/env python3
"""
ChromaDB ürün index'ini PostgreSQL'den (yeniden) oluşturur.

Ne yapar:
  - Tüm aktif ürünleri (id, name, brand, category, store_id, user_id) çeker.
  - product_index.upsert_products ile ChromaDB'ye batch'ler halinde basar (upsert,
    idempotent — tekrar çalıştırınca bozulmaz, günceller).
  - Sonunda doğrulama: kayıt sayısı + örnek arama.

Çalıştırma:
    uv run python reindex_products.py            # tüm kullanıcılar
    uv run python reindex_products.py --user 2   # sadece tek kullanıcı (hızlı test)
    uv run python reindex_products.py --reset    # önce koleksiyonu sıfırla, sonra bas

Not: Embedding maliyeti düşüktür (ürün başına ~30 token, ~$0.02/1M). 1000 ürün ~$0.0006.
Yeniden embed yalnızca bu script veya CRUD hook çağrılınca olur; sorgularda olmaz.
"""
from __future__ import annotations

import argparse
import sys
import time

from env_bootstrap import load_app_env

load_app_env()

# Mapper bootstrap (SQLAlchemy string-relationship çözümü için import şart)
from app.core.database import SessionLocal  # noqa: E402
from app.models.product import Product  # noqa: E402
from app.models.store import Store  # noqa: E402
from app.models.product_image import ProductImage  # noqa: E402,F401
from app.models.product_review import ProductReview  # noqa: E402,F401
from app.models.product_faq import ProductFaq  # noqa: E402,F401
from app.models.product_metrics_weekly import ProductMetricsWeekly  # noqa: E402,F401
from sqlalchemy import select  # noqa: E402

from app.services import product_index as pidx  # noqa: E402

BATCH = 100


def fetch_products(user_id: int | None) -> list[dict]:
    """Ürünleri store+user join ile çeker. user_id verilirse o tenant'a daraltır."""
    out: list[dict] = []
    with SessionLocal() as s:
        stmt = (
            select(
                Product.id, Product.name, Product.brand, Product.category,
                Product.store_id, Store.user_id,
            )
            .join(Store, Product.store_id == Store.id)
        )
        # is_active sütunu varsa yalnızca aktifleri al (yoksa sorun değil, atla)
        if hasattr(Product, "is_active"):
            stmt = stmt.where(Product.is_active == True)  # noqa: E712
        if user_id is not None:
            stmt = stmt.where(Store.user_id == int(user_id))
        for row in s.execute(stmt).all():
            pid, name, brand, category, store_id, uid = row
            out.append({
                "id": pid, "name": name, "brand": brand, "category": category,
                "store_id": store_id, "user_id": uid,
            })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", type=int, default=None, help="Sadece bu user_id")
    ap.add_argument("--reset", action="store_true", help="Önce koleksiyonu sıfırla")
    args = ap.parse_args()

    print("=" * 60)
    print("ChromaDB ürün reindex")
    print("=" * 60)
    print("Index yolu:", pidx.collection_stats().get("path"))

    if args.reset:
        try:
            col = pidx._get_collection()
            name = col.name
            pidx._client.delete_collection(name)
            pidx._collection = None  # singleton'ı sıfırla, get_or_create yeniden kursun
            print(f"[reset] '{name}' koleksiyonu silindi, sıfırdan kurulacak.")
        except Exception as exc:
            print(f"[reset] uyarı: {exc}")

    products = fetch_products(args.user)
    print(f"Çekilen ürün: {len(products)}"
          + (f" (user_id={args.user})" if args.user is not None else " (tüm kullanıcılar)"))
    if not products:
        print("Ürün yok, çıkılıyor.")
        return 0

    t0 = time.monotonic()
    total = 0
    for i in range(0, len(products), BATCH):
        chunk = products[i:i + BATCH]
        n = pidx.upsert_products(chunk)
        total += n
        print(f"  {total}/{len(products)} indekslendi...")
    dt = time.monotonic() - t0

    stats = pidx.collection_stats()
    print(f"\nBitti. {total} ürün {dt:.1f}s'de indekslendi.")
    print("Koleksiyon durumu:", stats)

    # ---- Doğrulama: birkaç örnek arama ----
    print("\n--- Doğrulama aramaları ---")
    sample_user = args.user if args.user is not None else products[0]["user_id"]
    for q in ["gitar", "saç bakım", "kolajen vitamin", "klavye"]:
        hits = pidx.search(q, user_id=sample_user, n=2)
        top = ", ".join(f"{h['name'][:40]} ({h['distance']:.2f})" for h in hits) or "(sonuç yok)"
        print(f"  [user={sample_user}] '{q}' → {top}")

    return 0


if __name__ == "__main__":
    sys.exit(main())