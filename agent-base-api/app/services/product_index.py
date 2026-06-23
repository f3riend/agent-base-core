"""
Ürün vektör index'i (ChromaDB, embedded/PersistentClient — ayrı servis/port YOK).

Amaç: resolver'ın büyük katalogda ölçeklenmesi. Tüm kataloğu LLM'e yollamak
yerine, soruya en yakın ~N ürünü vektör aramayla bulup yalnızca onları resolver'a
veririz → token katalog büyüklüğünden BAĞIMSIZ sabit kalır.

Kral / Sadrazam izolasyonu (kod-seviyesi, LLM'e güvenmez):
  - search(query, user_id=X)  → SADECE o tenant'ın ürünleri (metadata where filtresi)
  - search(query, user_id=None) → TÜM platform (kral/admin görünümü)
Bu, SQL tarafındaki _enforce_scope ile aynı felsefe: izolasyon veri katmanında zorlanır.

Kalıcılık: veri CHROMA_PATH dizinine yazılır (compose'da kalıcı volume'a bağlanmalı,
yoksa container restart'ında index uçar). Embedding OpenAI üzerinden (embeddings.py).
"""
from __future__ import annotations

import os
import threading
from typing import Optional

from app.services.embeddings import embed_texts, product_embedding_text

CHROMA_PATH = os.environ.get("CHROMA_PATH", "./chroma_data")
COLLECTION = os.environ.get("CHROMA_COLLECTION", "products")

_client = None
_collection = None
_lock = threading.Lock()


def _get_collection():
    """Tekil (singleton) koleksiyon. ChromaDB'nin kendi embedding fonksiyonunu
    KULLANMAYIZ — embedding'leri kendimiz OpenAI ile üretip embeddings= olarak
    veririz (kalite + tutarlılık). Bu yüzden embedding_function tanımlanmaz."""
    global _client, _collection
    if _collection is None:
        with _lock:
            if _collection is None:
                import chromadb
                _client = chromadb.PersistentClient(path=CHROMA_PATH)
                _collection = _client.get_or_create_collection(
                    name=COLLECTION,
                    metadata={"hnsw:space": "cosine"},  # kosinüs benzerliği
                )
    return _collection


def upsert_products(products: list[dict]) -> int:
    """Ürünleri index'e ekler/günceller. Her product dict:
      {id, name, brand, category, store_id, user_id}
    embeddings.py ile aynı product_embedding_text kullanılır (tutarlılık).
    Döner: işlenen ürün sayısı."""
    if not products:
        return 0
    col = _get_collection()
    ids = [str(p["id"]) for p in products]
    docs = [
        product_embedding_text(p.get("name", ""), p.get("brand"), p.get("category"))
        for p in products
    ]
    metas = [
        {
            "product_id": str(p["id"]),
            "store_id": str(p["store_id"]),
            "user_id": int(p["user_id"]),
            "name": p.get("name", ""),
        }
        for p in products
    ]
    vectors = embed_texts(docs)
    # upsert: var olan id güncellenir, yoksa eklenir
    col.upsert(ids=ids, embeddings=vectors, metadatas=metas, documents=docs)
    return len(ids)


def delete_product(product_id) -> None:
    """Bir ürünü index'ten siler (ürün CRUD silme hook'undan çağrılır)."""
    col = _get_collection()
    col.delete(ids=[str(product_id)])


def search(query: str, *, user_id: Optional[int] = None,
           store_ids: Optional[list[str]] = None, n: int = 20) -> list[dict]:
    """Soruya en yakın ürünleri döndürür.
      user_id verilirse  → SADECE o tenant (sadrazam).
      user_id=None       → tüm platform (kral/admin).
      store_ids verilirse → o mağazalara daralt (kralın 'şu eyalete bak'ı veya
                            çok-mağazalı sadrazamın alt kümesi).
    Döner: [{product_id, store_id, user_id, name, distance}], en yakın önce."""
    col = _get_collection()

    # ---- Scope filtresi (kod-seviyesi izolasyon) ----
    where = None
    conditions = []
    if user_id is not None:
        conditions.append({"user_id": int(user_id)})
    if store_ids:
        conditions.append({"store_id": {"$in": [str(s) for s in store_ids]}})
    if len(conditions) == 1:
        where = conditions[0]
    elif len(conditions) > 1:
        where = {"$and": conditions}
    # where None ise → filtre yok = kral (tüm platform)

    qvec = embed_texts([query])[0]
    res = col.query(
        query_embeddings=[qvec],
        n_results=n,
        where=where,
        include=["metadatas", "distances"],
    )
    out: list[dict] = []
    metas = (res.get("metadatas") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]
    for meta, dist in zip(metas, dists):
        out.append({
            "product_id": meta.get("product_id"),
            "store_id": meta.get("store_id"),
            "user_id": meta.get("user_id"),
            "name": meta.get("name"),
            "distance": dist,
        })
    return out


def collection_stats() -> dict:
    """Index durumu (reindex doğrulaması için)."""
    col = _get_collection()
    return {"count": col.count(), "path": CHROMA_PATH, "collection": COLLECTION}