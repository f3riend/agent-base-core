"""Eski mock veri temizleyici.

social_documents tablosundaki belirli koleksiyonları siler. Tablonun kendisi
silinmez. CLI olarak çağrılabilir:

    python -m app.services.cleanup_mock_data
"""
from __future__ import annotations

import logging
from typing import Iterable

from sqlalchemy import delete

from app.core.database import SessionLocal
from app.models.social_document import SocialDocument


MOCK_COLLECTIONS: tuple[str, ...] = (
    "products",
    "stores_runtime",
    "mock_stores_runtime",
    "mock_products_runtime",
    "product_reviews",
    "product_faq",
    "product_support_tickets",
    "product_metrics_daily",
    "product_assets",
)


def cleanup(collections: Iterable[str] = MOCK_COLLECTIONS) -> dict[str, int]:
    """Verilen koleksiyonlardaki tüm satırları siler. Silinen kayıt sayısını döner."""
    deleted: dict[str, int] = {}
    with SessionLocal() as session:
        for coll in collections:
            res = session.execute(
                delete(SocialDocument).where(SocialDocument.collection == coll)
            )
            deleted[coll] = int(res.rowcount or 0)
        session.commit()
    return deleted


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    result = cleanup()
    total = sum(result.values())
    for coll, count in result.items():
        logging.info(f"  - {coll}: {count}")
    logging.info(f"Toplam silinen: {total}")
