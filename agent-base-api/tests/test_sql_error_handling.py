"""Smoke test — SQL exception ile başarılı 0-satır sonucu birbirinden ayrılıyor mu?

Üç assertion:
  1. nl_to_sql._empty() is_error=True döner, error alanı dolu.
  2. nl_to_sql nl_to_sql() başarılı 0-satır yolunda is_error=False, error=None döner
     (SessionLocal'ı monkeypatch ederek gerçek DB'ye gitmeden simüle edilir).
  3. business_chat._compose_context_text() exception pg_ctx'sinde "teknik sorun"
     mesajı, başarılı boş pg_ctx'sinde "kayıt yok" mesajı üretir — ikisi farklı.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_empty_marks_error() -> None:
    from app.services import nl_to_sql as nlts

    out = nlts._empty("Sorgu hatası: bind parameter eksik")
    assert out["is_error"] is True, f"_empty() is_error=True bekleniyor, got {out['is_error']!r}"
    assert out["error"] == "Sorgu hatası: bind parameter eksik"
    assert out["row_count"] == 0
    print("  OK  _empty() is_error=True, error dolu")


def test_success_empty_rows_not_error() -> None:
    """nl_to_sql() başarılı yolda (exec başarılı, 0 satır) is_error=False döner."""
    from unittest.mock import MagicMock, patch
    from app.services import nl_to_sql as nlts

    fake_result = MagicMock()
    fake_result.keys.return_value = ["name"]
    fake_result.fetchall.return_value = []

    fake_session = MagicMock()
    fake_session.execute.return_value = fake_result

    fake_ctx = MagicMock()
    fake_ctx.__enter__.return_value = fake_session
    fake_ctx.__exit__.return_value = False

    fake_module = MagicMock()
    fake_module.SessionLocal.return_value = fake_ctx

    with patch.dict(sys.modules, {"app.core.database": fake_module}):
        with patch.object(
            nlts, "_match_template",
            return_value={
                "sql": "SELECT name FROM products WHERE store_id = ANY(CAST(:store_ids AS uuid[]))",
                "description": "test",
            },
        ):
            out = nlts.nl_to_sql(
                question="stok ne",
                store_ids=["00000000-0000-0000-0000-000000000000"],
                user_id=1,
                api_key="sk-test",
            )

    assert out["is_error"] is False, f"is_error=False bekleniyor, got {out['is_error']!r}"
    assert out["error"] is None, f"error=None bekleniyor, got {out['error']!r}"
    assert out["row_count"] == 0
    print("  OK  Başarılı 0-satır yolu is_error=False, error=None")


def test_sql_exception_path_marks_error() -> None:
    """nl_to_sql() exception fırlatan SessionLocal ile is_error=True döner."""
    from unittest.mock import MagicMock, patch
    from app.services import nl_to_sql as nlts

    fake_session = MagicMock()
    fake_session.execute.side_effect = RuntimeError(
        "A value is required for bind parameter 'product_id'"
    )

    fake_ctx = MagicMock()
    fake_ctx.__enter__.return_value = fake_session
    fake_ctx.__exit__.return_value = False

    fake_module = MagicMock()
    fake_module.SessionLocal.return_value = fake_ctx

    with patch.dict(sys.modules, {"app.core.database": fake_module}):
        with patch.object(
            nlts, "_match_template",
            return_value={
                "sql": "SELECT name FROM products WHERE id = :product_id",
                "description": "test",
            },
        ):
            out = nlts.nl_to_sql(
                question="bu ürün ne",
                store_ids=["00000000-0000-0000-0000-000000000000"],
                user_id=1,
                api_key="sk-test",
            )

    assert out["is_error"] is True, f"is_error=True bekleniyor, got {out['is_error']!r}"
    assert "bind parameter" in (out["error"] or ""), f"error 'bind parameter' içermeli: {out['error']!r}"
    assert out["row_count"] == 0
    print("  OK  SQL exception yolu is_error=True, error dolu")


def test_context_text_distinguishes_error_from_empty() -> None:
    """business_chat._compose_context_text iki durum için FARKLI metin üretir."""
    import business_chat as bc

    pg_ctx_error = {
        "type": "smart_context",
        "text": "",
        "sql": "",
        "description": "Sorgu hatası: column s.stock_quantity does not exist",
        "row_count": 0,
        "error": "Sorgu hatası: column s.stock_quantity does not exist",
        "is_error": True,
    }

    pg_ctx_empty_success = {
        "type": "smart_context",
        "text": "",
        "sql": "SELECT name FROM products WHERE 1=0",
        "description": "Hiç eşleşmedi",
        "row_count": 0,
        "error": None,
        "is_error": False,
    }

    pg_ctx_with_data = {
        "type": "smart_context",
        "text": "name: Logitech | stock_quantity: 42",
        "sql": "SELECT name, stock_quantity FROM products LIMIT 1",
        "description": "Stok",
        "row_count": 1,
        "error": None,
        "is_error": False,
    }

    text_error = bc._compose_context_text(pg_ctx_error)
    text_empty = bc._compose_context_text(pg_ctx_empty_success)
    text_data = bc._compose_context_text(pg_ctx_with_data)

    print()
    print("  --- SQL hatası dalı ---")
    print(f"  {text_error}")
    print("  --- Başarılı 0-satır dalı ---")
    print(f"  {text_empty}")
    print("  --- Veri dolu dalı ---")
    print(f"  {text_data}")
    print()

    assert text_error != text_empty, "Hata ve boş-sonuç AYNI metni üretiyor — sorun düzelmemiş!"
    assert "teknik" in text_error.lower() or "hata" in text_error.lower(), (
        f"Hata metni 'teknik' veya 'hata' içermeli: {text_error!r}"
    )
    assert "icat etme" in text_error.lower() or "i̇cat etme" in text_error.lower(), (
        f"Hata metni 'icat etme' uyarısı içermeli: {text_error!r}"
    )
    assert "kayıt bulunamadı" in text_empty.lower(), (
        f"Boş sonuç metni 'kayıt bulunamadı' içermeli: {text_empty!r}"
    )
    assert "DB VERİSİ" in text_data, f"Veri metni 'DB VERİSİ' içermeli: {text_data!r}"
    print("  OK  Üç dal birbirinden net şekilde ayrılıyor")


def main() -> int:
    os.environ.setdefault("OPENAI_API_KEY", "sk-test-dummy")
    tests = [
        ("test_empty_marks_error", test_empty_marks_error),
        ("test_success_empty_rows_not_error", test_success_empty_rows_not_error),
        ("test_sql_exception_path_marks_error", test_sql_exception_path_marks_error),
        ("test_context_text_distinguishes_error_from_empty", test_context_text_distinguishes_error_from_empty),
    ]
    failed = 0
    for name, fn in tests:
        print(f"[{name}]")
        try:
            fn()
        except AssertionError as e:
            print(f"  FAIL  {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR  {type(e).__name__}: {e}")
            failed += 1
    print()
    if failed:
        print(f"SONUÇ: {failed} test başarısız")
        return 1
    print(f"SONUÇ: {len(tests)}/{len(tests)} test geçti")
    return 0


if __name__ == "__main__":
    sys.exit(main())
