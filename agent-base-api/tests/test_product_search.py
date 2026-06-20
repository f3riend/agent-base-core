"""Gerçek PostgreSQL ile _one_shot_find_product ve _extract_product_search_terms testi.

Mock değil — gerçek SessionLocal kullanılır. .env'deki DATABASE_URL'in çalışıyor
olması gerekir. Senaryolar:
  1. Var olmayan ürün (Pantene saç yağı): None dönmeli.
  2. DB'deki gerçek bir ürünün adıyla: doğru id ile eşleşmeli.
  3. DB'deki gerçek bir markayla (Razer/Anker vb.): o markadan bir ürün dönmeli.
  4. Stop-word filtrelemesi: komut kelimeleri arama terimlerinden çıkmalı.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")


TEST_USER_ID = 1


def _fetch_sample_products(limit: int = 5) -> list[dict]:
    """DB'den gerçek ürünleri çek (test için ground truth)."""
    from app.core.database import SessionLocal
    from sqlalchemy import text

    with SessionLocal() as session:
        rows = session.execute(
            text(
                "SELECT p.id::text AS id, p.name, p.brand "
                "FROM products p JOIN stores s ON s.id = p.store_id "
                "WHERE s.user_id = :uid LIMIT :lim"
            ),
            {"uid": TEST_USER_ID, "lim": limit},
        ).fetchall()
        return [{"id": r.id, "name": r.name, "brand": r.brand} for r in rows]


def test_stopwords_filtered() -> None:
    """'için', 'bir', 'kampanya', 'oluştur' gibi kelimeler search_terms'den çıkmalı."""
    from business_chat import _extract_product_search_terms

    terms = _extract_product_search_terms(
        "Anker Soundcore P40i için %20 indirimli bir kampanya oluştur"
    )
    print(f"  search_terms: {terms}")

    must_be_present = {"anker", "soundcore", "p40i"}
    must_be_absent = {"için", "bir", "indirimli", "kampanya", "oluştur", "20"}

    missing_required = must_be_present - set(terms)
    leaked_stopwords = must_be_absent & set(terms)

    assert not missing_required, f"Eksik kritik terimler: {missing_required}"
    assert not leaked_stopwords, f"Stop-word'ler sızdı: {leaked_stopwords}"
    print("  OK  Stop-word'ler temiz, ürün-spesifik terimler korundu")


def test_nonexistent_product_returns_none() -> None:
    """'Pantene saç yağı' DB'de yok → None dönmeli, asla başka bir ürün dönmemeli."""
    from business_chat import _one_shot_find_product

    result = _one_shot_find_product(
        TEST_USER_ID,
        "Pantene saç yağı için %20 indirimli bir kampanya oluştur",
    )

    if result is not None:
        print(f"  HATALI EŞLEŞME: name={result.get('name')!r}, brand={result.get('brand')!r}")
    assert result is None, (
        f"None bekleniyor (Pantene DB'de yok), got name={result.get('name')!r} "
        f"brand={result.get('brand')!r} — eski bug geri gelmiş!"
    )
    print("  OK  Pantene için None döndü; alakasız ürün eşleştirilmedi")


def test_nonexistent_brand_only_returns_none() -> None:
    """Tamamen uydurma bir marka: 'Xanthippe pomadı için kampanya' → None."""
    from business_chat import _one_shot_find_product

    result = _one_shot_find_product(
        TEST_USER_ID,
        "Xanthippe pomadı için kampanya oluştur",
    )
    assert result is None, f"Uydurma marka için None bekleniyor, got: {result}"
    print("  OK  Uydurma marka için None döndü")


def test_real_product_by_name() -> None:
    """DB'den gerçek bir ürün al, adıyla sor → doğru id eşleşmeli."""
    from business_chat import _one_shot_find_product

    samples = _fetch_sample_products(limit=8)
    print(f"  DB'den örnek ürünler (ilk 3):")
    for s in samples[:3]:
        print(f"    - {s['id'][:8]}... | brand={s['brand']!r} | name={s['name'][:60]!r}")

    target = None
    for s in samples:
        if "Anker" in (s["brand"] or "") and "Soundcore" in (s["name"] or ""):
            target = s
            break
    if target is None:
        target = next((s for s in samples if "Anker" in (s["brand"] or "")), None)
    if target is None:
        target = samples[0]

    print(f"  Hedef: id={target['id'][:8]}... brand={target['brand']!r} name={target['name'][:60]!r}")

    first_two_words = " ".join((target["name"] or "").split()[:2])
    question = f"{first_two_words} için kampanya oluştur"
    print(f"  Soru: {question!r}")

    result = _one_shot_find_product(TEST_USER_ID, question)
    assert result is not None, f"Gerçek ürün için sonuç bekleniyor, None döndü"
    assert result["id"] == target["id"], (
        f"id eşleşmedi: beklenen={target['id']}, dönen={result['id']}"
    )
    print(f"  OK  id eşleşti: {result['id']}")


def test_real_brand_returns_that_brand() -> None:
    """'Razer Cobra için kampanya oluştur' → Razer Cobra ürünü dönmeli.

    NOT: Yeni katı politika (score>=2 AND ratio>=0.5) tek-terim sorguları
    (sadece 'Razer') reddediyor — common-word leak'i önlemek için. Marka +
    model gibi en az iki terim verildiğinde strong-match geçer.
    """
    from business_chat import _one_shot_find_product

    result = _one_shot_find_product(TEST_USER_ID, "Razer Cobra için kampanya oluştur")
    assert result is not None, (
        "Razer Cobra (marka + model) için None döndü, oysa DB'de bu ürün var"
    )
    brand = (result.get("brand") or "").lower()
    name = (result.get("name") or "").lower()
    assert "razer" in brand or "razer" in name, (
        f"Razer Cobra aranırken farklı marka döndü: brand={result.get('brand')!r}, "
        f"name={result.get('name')!r}"
    )
    assert "cobra" in name, f"Razer Cobra bekleniyor, got: {result.get('name')!r}"
    print(f"  OK  'Razer Cobra' → {result.get('name')!r}")


def test_relevance_picks_best_when_multiple_candidates() -> None:
    """Birden fazla aday varsa name/brand'de en çok terim eşleşeni seçmeli."""
    from business_chat import _one_shot_find_product

    result = _one_shot_find_product(TEST_USER_ID, "Logitech webcam için kampanya")
    assert result is not None, "Logitech webcam için sonuç bekleniyor"
    brand = (result.get("brand") or "").lower()
    name = (result.get("name") or "").lower()
    has_logitech = "logitech" in brand or "logitech" in name
    has_webcam = "webcam" in name or "camera" in name
    assert has_logitech, f"Logitech eşleşmesi yok: {result.get('name')!r}"
    assert has_webcam, (
        f"Logitech-Logitech eşleşti ama webcam değil: {result.get('name')!r} "
        "— relevance skoru webcam'i öne almalıydı"
    )
    print(f"  OK  En relevant aday seçildi: {result.get('name')!r}")


def main() -> int:
    os.environ.setdefault("OPENAI_API_KEY", "sk-test-dummy")
    tests = [
        ("test_stopwords_filtered", test_stopwords_filtered),
        ("test_nonexistent_product_returns_none", test_nonexistent_product_returns_none),
        ("test_nonexistent_brand_only_returns_none", test_nonexistent_brand_only_returns_none),
        ("test_real_product_by_name", test_real_product_by_name),
        ("test_real_brand_returns_that_brand", test_real_brand_returns_that_brand),
        ("test_relevance_picks_best_when_multiple_candidates", test_relevance_picks_best_when_multiple_candidates),
    ]
    failed = 0
    for name, fn in tests:
        print(f"\n[{name}]")
        try:
            fn()
        except AssertionError as e:
            print(f"  FAIL  {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR  {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    print()
    if failed:
        print(f"SONUÇ: {failed}/{len(tests)} test başarısız")
        return 1
    print(f"SONUÇ: {len(tests)}/{len(tests)} test geçti")
    return 0


if __name__ == "__main__":
    sys.exit(main())
