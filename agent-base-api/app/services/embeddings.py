"""
Embedding üretimi — tek giriş noktası (embedding-agnostik).

Şu an OpenAI `text-embedding-3-small` kullanır (kaliteli Türkçe, ekosistem uyumu,
neredeyse sıfır maliyet: sadece ürün adı/marka/kategori embed edilir, bir kez).

İleride tam-yerel modele (Diablo vizyonu) geçmek istenirse SADECE bu dosya
değişir; ChromaDB/resolver tarafı bu fonksiyonun arkasında soyutlanmıştır.

Maliyet notu: text-embedding-3-small ~$0.02/1M token. Bir ürün ~30 token →
10.000 ürünün ilk indekslenmesi ~$0.006. Sorgu embed'i ~15 token → pratikte sıfır.
Embedding'ler ChromaDB'de diske yazılır; tekrar üretilmez (her sorguda yeniden
embed YOK — yalnızca yeni/değişen ürün ve gelen sorunun kendisi embed edilir).
"""
from __future__ import annotations

import os
from typing import Sequence

EMBED_MODEL = os.environ.get("EMBED_MODEL", "text-embedding-3-small")
EMBED_DIM = 1536  # text-embedding-3-small boyutu (sabit; modeli değiştirirsen güncelle)

_client = None


def _get_client():
    global _client
    if _client is None:
        from openai import OpenAI
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY yok — embedding üretilemez.")
        _client = OpenAI(api_key=api_key, timeout=30)
    return _client


def embed_texts(texts: Sequence[str]) -> list[list[float]]:
    """Birden fazla metni tek API çağrısında embed eder (batch — verimli).
    Boş/None metinler boş string'e indirgenir (OpenAI boş string kabul etmez,
    bu yüzden en az bir boşluk gönderilir)."""
    if not texts:
        return []
    cleaned = [(t or "").strip() or " " for t in texts]
    client = _get_client()
    resp = client.embeddings.create(model=EMBED_MODEL, input=cleaned)
    # resp.data sırası input sırasıyla aynıdır
    return [d.embedding for d in resp.data]


def embed_text(text: str) -> list[float]:
    """Tek metin için kısayol (sorgu embed'i gibi)."""
    return embed_texts([text])[0]


def product_embedding_text(name: str, brand: str | None = None,
                           category: str | None = None) -> str:
    """Bir ürünü temsil eden embedding metni. Ad + marka + kategori birleşik.
    Tutarlılık için indeksleme ve (gerekirse) sorgu tarafında AYNI fonksiyon
    kullanılmalı."""
    parts = [name or ""]
    if brand:
        parts.append(brand)
    if category:
        parts.append(category)
    return " ".join(p.strip() for p in parts if p and p.strip())