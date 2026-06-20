"""End-to-end coreference test — gerçek PostgreSQL ile multi-turn pronoun threading.

Mock değil — gerçek conversation_memory + business_query_router kullanılır.
nl_to_sql LLM çağrısı atlanır (template eşleşmesi ile veya gerekirse mock'lanır).

Senaryolar:
  1. record_turn → conversation_context: primary_entity_label DB'ye yazılıyor
     ve okunduğunda aynı dönüyor.
  2. Tek tur: "Anker Soundcore stok durumu" → primary_entity yazılıyor.
  3. İki tur: "Anker stok durumu", sonra "Bu ürünün fiyatı" — ikinci turdaki
     route() Anker label'ını alıp soruyu rewrite ediyor, nl_to_sql'e giden
     soru "Anker Soundcore..." içeriyor.
  4. Üç tur: Anker → Logitech → "Bu ürünün yorumları" — üçüncü tur
     Logitech'e bağlanıyor (en son aktif), Anker'a değil.
  5. Hiç pronoun yoksa rewrite yapmıyor.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

TEST_USER_ID = 1


def _new_session(user_id: int = TEST_USER_ID) -> str:
    import conversation_memory as cm
    sid = f"sess_test_{uuid.uuid4().hex[:12]}"
    cm.open_session(user_id=user_id, session_id=sid)
    return sid


def _purge_session(sid: str) -> None:
    """Test sonrası bchat_turns + bchat_sessions kayıtlarını sil."""
    from app.core.database import SessionLocal
    from sqlalchemy import text
    try:
        with SessionLocal() as s:
            s.execute(text("DELETE FROM bchat_turns WHERE session_id = :sid"), {"sid": sid})
            s.execute(text("DELETE FROM bchat_sessions WHERE id = :sid"), {"sid": sid})
            s.commit()
    except Exception as exc:
        print(f"  [cleanup] {exc}")


def _fetch_real_product(name_pattern: str) -> dict:
    """Test için gerçek bir ürünün id/name/brand bilgisini al."""
    from app.core.database import SessionLocal
    from sqlalchemy import text
    with SessionLocal() as s:
        row = s.execute(
            text(
                "SELECT p.id::text AS id, p.name, p.brand "
                "FROM products p JOIN stores st ON st.id = p.store_id "
                "WHERE st.user_id = :uid AND p.name ILIKE :pat LIMIT 1"
            ),
            {"uid": TEST_USER_ID, "pat": f"%{name_pattern}%"},
        ).first()
        assert row is not None, f"Test ön-koşulu: '{name_pattern}' içeren ürün bulunamadı"
        return {"id": row.id, "name": row.name, "brand": row.brand}


def test_record_turn_persists_entity_and_context_reads_it() -> None:
    """primary_entity_* alanları INSERT'e gidiyor ve conversation_context görüyor."""
    import conversation_memory as cm

    sid = _new_session()
    try:
        cm.record_turn(
            session_id=sid, user_id=TEST_USER_ID,
            question="Test sorusu", answer="Test cevabı",
            intent="nl_query",
            primary_entity_type="product",
            primary_entity_id="abc-123-xyz",
            primary_entity_label="Anker Soundcore P40i ANC Bluetooth Kulaklık",
        )
        ctx = cm.conversation_context(sid)
        print(f"  active_entity_label = {ctx['active_entity_label']!r}")
        print(f"  active_entity_id    = {ctx['active_entity_id']!r}")
        print(f"  active_entity_type  = {ctx['active_entity_type']!r}")
        assert ctx["active_entity_label"] == "Anker Soundcore P40i ANC Bluetooth Kulaklık"
        assert ctx["active_entity_id"] == "abc-123-xyz"
        assert ctx["active_entity_type"] == "product"
        print("  OK  record_turn → conversation_context round-trip çalışıyor")
    finally:
        _purge_session(sid)


def test_conversation_context_returns_most_recent_entity() -> None:
    """Aynı session'da 3 turn — context en son non-null entity'yi okumalı."""
    import conversation_memory as cm

    sid = _new_session()
    try:
        cm.record_turn(
            session_id=sid, user_id=TEST_USER_ID,
            question="Anker stok?", answer="...",
            primary_entity_type="product",
            primary_entity_id="anker-id",
            primary_entity_label="Anker Soundcore P40i",
        )
        cm.record_turn(
            session_id=sid, user_id=TEST_USER_ID,
            question="Logitech webcam stok?", answer="...",
            primary_entity_type="product",
            primary_entity_id="logitech-id",
            primary_entity_label="Logitech C920 HD Pro Webcam 1080p",
        )
        # Bir entity-siz tur — bu, en son non-null kaydı (Logitech) etkilememeli
        cm.record_turn(
            session_id=sid, user_id=TEST_USER_ID,
            question="Genel durum nasıl?", answer="...",
            primary_entity_type=None,
            primary_entity_id=None,
            primary_entity_label=None,
        )
        ctx = cm.conversation_context(sid)
        print(f"  En son non-null entity: {ctx['active_entity_label']!r}")
        assert ctx["active_entity_label"] == "Logitech C920 HD Pro Webcam 1080p", (
            f"En son non-null entity Logitech olmalı, got {ctx['active_entity_label']!r}"
        )
        print("  OK  En son non-null entity doğru seçildi (Anker üstüne yazıldı)")
    finally:
        _purge_session(sid)


def test_route_rewrites_pronoun_with_active_entity() -> None:
    """route() pronoun + active_entity → nl_to_sql'e giden soru zenginleşmeli."""
    import business_query_router as bqr

    captured: dict = {}

    def fake_nl_to_sql(*, question, store_ids, user_id, api_key=None, is_admin=False):
        captured["question"] = question
        return {
            "rows": [], "formatted": "", "sql": "",
            "description": "test", "model_tier": "mini",
            "row_count": 0, "error": None, "is_error": False,
        }

    with patch("app.services.nl_to_sql.nl_to_sql", side_effect=fake_nl_to_sql):
        result = bqr.route(
            "Bu ürünün ortalama puanı kaç?",
            user_id=TEST_USER_ID,
            session_id="sess_test_dummy",
            active_entity_label="Anker Soundcore P40i ANC Bluetooth Kulaklık",
        )

    sent = captured.get("question", "")
    print(f"  nl_to_sql'e giden soru: {sent!r}")
    print(f"  pronoun_rewritten flag: {result['data'].get('pronoun_rewritten')}")
    assert "Anker Soundcore" in sent, f"Rewrite başarısız — Anker eklenmemiş: {sent!r}"
    assert "bu ürünün" not in sent.lower() or '"anker' in sent.lower(), (
        f"Pronoun'a ek olarak '{sent}' içinde rewrite görünmüyor"
    )
    assert result["data"].get("pronoun_rewritten") is True
    print("  OK  route() pronoun'u Anker ile rewrite etti, nl_to_sql doğru soruyu aldı")


def test_route_no_rewrite_when_no_pronoun() -> None:
    """Pronoun yoksa route() soruyu olduğu gibi geçirir."""
    import business_query_router as bqr

    captured: dict = {}

    def fake_nl_to_sql(*, question, store_ids, user_id, api_key=None, is_admin=False):
        captured["question"] = question
        return {
            "rows": [], "formatted": "", "sql": "",
            "description": "", "model_tier": "mini",
            "row_count": 0, "error": None, "is_error": False,
        }

    with patch("app.services.nl_to_sql.nl_to_sql", side_effect=fake_nl_to_sql):
        result = bqr.route(
            "Sistemde kaç ürün var?",
            user_id=TEST_USER_ID,
            session_id="sess_test_dummy",
            active_entity_label="Anker Soundcore P40i",
        )

    sent = captured.get("question", "")
    print(f"  nl_to_sql'e giden soru: {sent!r}")
    assert "Anker" not in sent, f"Pronoun yokken Anker eklenmemeli, got: {sent!r}"
    assert result["data"].get("pronoun_rewritten") is False
    print("  OK  Pronoun olmayınca route() rewriting yapmıyor")


def test_three_turn_pronoun_resolves_to_most_recent_product() -> None:
    """3-tur senaryo: Anker → Logitech → 'Bu ürünün yorumları' = Logitech."""
    import conversation_memory as cm
    import business_query_router as bqr

    sid = _new_session()
    try:
        anker = _fetch_real_product("Anker Soundcore")
        logitech = _fetch_real_product("Logitech C920")
        print(f"  Anker:    {anker['id']}  {anker['name'][:50]}")
        print(f"  Logitech: {logitech['id']}  {logitech['name'][:50]}")

        cm.record_turn(
            session_id=sid, user_id=TEST_USER_ID,
            question="Anker Soundcore stok durumu ne?",
            answer="50 adet stok var.",
            intent="nl_query",
            primary_entity_type="product",
            primary_entity_id=anker["id"],
            primary_entity_label=anker["name"],
        )
        cm.record_turn(
            session_id=sid, user_id=TEST_USER_ID,
            question="Logitech webcam fiyatı ne?",
            answer="850 TL.",
            intent="nl_query",
            primary_entity_type="product",
            primary_entity_id=logitech["id"],
            primary_entity_label=logitech["name"],
        )

        ctx = cm.conversation_context(sid)
        print(f"  Aktif entity (3. tur öncesi): {ctx['active_entity_label']!r}")
        assert ctx["active_entity_label"] == logitech["name"], (
            f"Aktif entity Logitech olmalı, got {ctx['active_entity_label']!r}"
        )

        captured: dict = {}
        def fake_nl_to_sql(*, question, store_ids, user_id, api_key=None, is_admin=False):
            captured["question"] = question
            return {
                "rows": [], "formatted": "", "sql": "",
                "description": "", "model_tier": "mini",
                "row_count": 0, "error": None, "is_error": False,
            }

        with patch("app.services.nl_to_sql.nl_to_sql", side_effect=fake_nl_to_sql):
            bqr.route(
                "Bu ürünün yorumları neler?",
                user_id=TEST_USER_ID,
                session_id=sid,
                active_entity_label=ctx["active_entity_label"],
            )

        sent = captured["question"]
        print(f"  3. tur nl_to_sql sorusu: {sent!r}")
        assert "Logitech" in sent, (
            f"3. tur Logitech'e bağlanmalıydı (en son aktif), got: {sent!r}"
        )
        assert "Anker" not in sent, (
            f"3. tur Anker'a bağlanmamalıydı (eski aktif), got: {sent!r}"
        )
        print("  OK  3. tur Logitech'e bağlandı, Anker'a değil — en son aktif kazandı")
    finally:
        _purge_session(sid)


def test_lookup_basic_product_finds_real_product() -> None:
    """answer_question'un kullandığı hafif lookup gerçek ürünü buluyor."""
    import business_chat as bc

    target = _fetch_real_product("Anker Soundcore")
    result = bc._lookup_basic_product(
        TEST_USER_ID,
        "Anker Soundcore'un stok durumu ne?",
    )
    assert result is not None, "Gerçek ürün için sonuç bekleniyor"
    assert result["id"] == target["id"], (
        f"id eşleşmeli: expected={target['id']}, got={result['id']}"
    )
    print(f"  OK  _lookup_basic_product Anker'i buldu: {result['name'][:50]}... ({result['id'][:8]}...)")


def test_lookup_basic_product_returns_none_for_pronoun_only_question() -> None:
    """'Bu ürünün fiyatı ne?' tek başına spesifik ürün adlandırmıyor → None."""
    import business_chat as bc

    result = bc._lookup_basic_product(TEST_USER_ID, "Bu ürünün fiyatı ne?")
    print(f"  _lookup_basic_product('Bu ürünün fiyatı ne?') = {result}")
    assert result is None, (
        f"Salt pronoun sorusu için None bekleniyor, got: {result}"
    )
    print("  OK  Salt pronoun → None (active_entity inherit edilecek)")


def main() -> int:
    os.environ.setdefault("OPENAI_API_KEY", "sk-test-dummy")
    tests = [
        ("test_record_turn_persists_entity_and_context_reads_it", test_record_turn_persists_entity_and_context_reads_it),
        ("test_conversation_context_returns_most_recent_entity", test_conversation_context_returns_most_recent_entity),
        ("test_route_rewrites_pronoun_with_active_entity", test_route_rewrites_pronoun_with_active_entity),
        ("test_route_no_rewrite_when_no_pronoun", test_route_no_rewrite_when_no_pronoun),
        ("test_three_turn_pronoun_resolves_to_most_recent_product", test_three_turn_pronoun_resolves_to_most_recent_product),
        ("test_lookup_basic_product_finds_real_product", test_lookup_basic_product_finds_real_product),
        ("test_lookup_basic_product_returns_none_for_pronoun_only_question", test_lookup_basic_product_returns_none_for_pronoun_only_question),
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
