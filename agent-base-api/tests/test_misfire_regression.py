"""Misfire regression — sess_8ab5827a... oturumundaki 5 gerçek senaryo.

Her biri kullanıcının HİÇ bahsetmediği bir ürünü active_entity_label olarak
yakalıyordu. Yeni word-boundary + strong-match eşiği (score>=2, ratio>=0.5)
sonrası hiçbiri yanlış ürün bulmamalı — hepsi None dönmeli.

Pozitif kontroller de var: gerçek ürün adı verilince doğru bulunmalı,
karar ağacında pronoun-rewrite zayıf lookup'ı ezmemeli.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

TEST_USER_ID = 1


def _new_session() -> str:
    import conversation_memory as cm
    sid = f"sess_test_{uuid.uuid4().hex[:12]}"
    cm.open_session(user_id=TEST_USER_ID, session_id=sid)
    return sid


def _purge_session(sid: str) -> None:
    from app.core.database import SessionLocal
    from sqlalchemy import text
    try:
        with SessionLocal() as s:
            s.execute(text("DELETE FROM bchat_turns WHERE session_id = :sid"), {"sid": sid})
            s.execute(text("DELETE FROM bchat_sessions WHERE id = :sid"), {"sid": sid})
            s.commit()
    except Exception as exc:
        print(f"  [cleanup] {exc}")


def test_misfire_1_pantene_tl_does_not_match_portlu() -> None:
    """243: 'Pantene saç yağının fiyatı kaç TL?' → eskiden 'tl'→'Portlu' (Baseus 65W)."""
    import business_chat as bc
    result = bc._lookup_basic_product(TEST_USER_ID, "Pantene saç yağının fiyatı kaç TL?")
    assert result is None, f"Pantene/TL yanlış eşleşmesi geri geldi: {result}"
    print("  OK  Pantene/TL → None ('tl' artık 'Portlu'ya match olmuyor)")


def test_misfire_2_cok_does_not_match_coklayici() -> None:
    """248: 'Müşteriler en çok neden şikayet ediyor?' → eskiden 'çok'→'Çoklayıcı'."""
    import business_chat as bc
    result = bc._lookup_basic_product(
        TEST_USER_ID, "Müşteriler en çok neden şikayet ediyor?"
    )
    assert result is None, f"'çok'→'Çoklayıcı' misfire'ı geri geldi: {result}"
    print("  OK  'çok' → None ('Çoklayıcı'da word boundary yok)")


def test_misfire_3_en_does_not_match_spigen() -> None:
    """250: 'Yorumlara göre bu ürünün en büyük zayıf noktası ne?' → eskiden 'en'→'Spigen'."""
    import business_chat as bc
    result = bc._lookup_basic_product(
        TEST_USER_ID, "Yorumlara göre bu ürünün en büyük zayıf noktası ne?"
    )
    assert result is None, f"'en'→'Spigen' misfire'ı geri geldi: {result}"
    print("  OK  'en' → None ('Spigen' içinde word boundary yok)")


def test_misfire_4_one_does_not_match_doner() -> None:
    """257: 'Bu ürünü öne çıkarmalı mıyım, talep nasıl?' → eskiden 'öne'→'Döner' (ESR)."""
    import business_chat as bc
    result = bc._lookup_basic_product(
        TEST_USER_ID, "Bu ürünü öne çıkarmalı mıyım, talep nasıl?"
    )
    assert result is None, f"'öne'→'Döner' misfire'ı geri geldi: {result}"
    print("  OK  'öne' → None ('Döner' içinde word boundary yok)")


def test_misfire_5_guven_does_not_match_guvenlik() -> None:
    """260: '...güven artırıcı bir kampanya' → eskiden 'güven'→'Güvenlik' (TP-Link Tapo)."""
    import business_chat as bc
    result = bc._lookup_basic_product(
        TEST_USER_ID,
        "Düşük puanlı yorumları göz önünde bulundurarak güven artırıcı bir kampanya...",
    )
    assert result is None, f"'güven'→'Güvenlik' misfire'ı geri geldi: {result}"
    print("  OK  'güven' → None ('Güvenlik' içinde word boundary yok)")


def test_positive_strong_match_still_works() -> None:
    """Gerçek ürün adı verildiğinde lookup yine de çalışmalı."""
    import business_chat as bc
    result = bc._lookup_basic_product(
        TEST_USER_ID, "Anker Soundcore P40i için indirim düşünüyorum"
    )
    assert result is not None, "Çok terimli güçlü match'in çalışması bekleniyor"
    assert "Anker" in (result["brand"] or "")
    print(f"  OK  'Anker Soundcore P40i' → {result['name'][:50]}...")


def test_positive_two_term_match_passes_threshold() -> None:
    """İki terim de name/brand'de birden eşleşirse (score=2, ratio=1.0) geçmeli."""
    import business_chat as bc
    result = bc._lookup_basic_product(TEST_USER_ID, "Logitech webcam stokları")
    assert result is not None, "Logitech webcam için sonuç bekleniyor"
    name = (result["name"] or "").lower()
    assert "logitech" in name and "webcam" in name, f"Yanlış aday: {result['name']}"
    print(f"  OK  'Logitech webcam' → {result['name']}")


def test_single_term_brand_query_rejected_under_new_strict_policy() -> None:
    """Yeni katı politika: tek terim score=1 — strong-match değil, None.

    Eskiden 'Razer için kampanya' Razer Cobra'yı bulurdu (score=1/1).
    Yeni eşik (score>=2) bunu reddediyor — kullanıcı daha spesifik olmalı.
    """
    import business_chat as bc
    result = bc._lookup_basic_product(TEST_USER_ID, "Razer için kampanya oluştur")
    assert result is None, (
        f"Tek-terim 'razer' artık reddedilmeli (score=1/1, eşik 2): {result}"
    )
    print("  OK  Tek-terim sorgu reddediliyor (yeni katı politika gereği)")


def test_decision_tree_pronoun_inheritance_not_overridden_by_weak_match() -> None:
    """257 desync turunu simüle et: prev_active=Spigen, pronoun var, soru 'öne'."""
    import conversation_memory as cm
    import business_query_router as bqr

    sid = _new_session()
    try:
        cm.record_turn(
            session_id=sid, user_id=TEST_USER_ID,
            question="Spigen ürünü hakkında soru", answer="...",
            primary_entity_type="product",
            primary_entity_id="spigen-id-xyz",
            primary_entity_label="Spigen Ultra Hybrid iPhone 15 Pro Kılıf Şeffaf",
        )
        ctx = cm.conversation_context(sid)
        assert ctx["active_entity_label"] == "Spigen Ultra Hybrid iPhone 15 Pro Kılıf Şeffaf"

        from unittest.mock import patch
        captured = {}
        def fake_nl_to_sql(*, question, store_ids, user_id, api_key=None, is_admin=False):
            captured["question"] = question
            return {
                "rows": [], "formatted": "", "sql": "",
                "description": "", "model_tier": "mini",
                "row_count": 0, "error": None, "is_error": False,
            }
        with patch("app.services.nl_to_sql.nl_to_sql", side_effect=fake_nl_to_sql):
            result = bqr.route(
                "Bu ürünü öne çıkarmalı mıyım, talep nasıl?",
                user_id=TEST_USER_ID,
                session_id=sid,
                active_entity_label=ctx["active_entity_label"],
                active_entity_id=ctx["active_entity_id"],
                active_entity_type=ctx["active_entity_type"],
            )

        data = result["data"]
        print(f"  pronoun_rewritten     = {data.get('pronoun_rewritten')}")
        print(f"  resolved_entity_label = {data.get('resolved_entity_label')!r}")
        print(f"  effective_question    = {data.get('effective_question')!r}")
        assert data.get("pronoun_rewritten") is True
        assert data.get("resolved_entity_label") == "Spigen Ultra Hybrid iPhone 15 Pro Kılıf Şeffaf"
        assert data.get("resolved_entity_id") == "spigen-id-xyz"
        assert "Spigen" in data.get("effective_question", "")
        # Old bug: ESR ('öne'→'Döner') would have overridden Spigen.
        # New: route() return PROVIDES the entity atomically, no separate lookup.
        assert "ESR" not in (data.get("resolved_entity_label") or "")
        print("  OK  Pronoun-rewrite Spigen'i korudu; zayıf 'öne' lookup'ı ezmedi")
    finally:
        _purge_session(sid)


def main() -> int:
    import os
    os.environ.setdefault("OPENAI_API_KEY", "sk-test-dummy")
    tests = [
        ("test_misfire_1_pantene_tl_does_not_match_portlu", test_misfire_1_pantene_tl_does_not_match_portlu),
        ("test_misfire_2_cok_does_not_match_coklayici", test_misfire_2_cok_does_not_match_coklayici),
        ("test_misfire_3_en_does_not_match_spigen", test_misfire_3_en_does_not_match_spigen),
        ("test_misfire_4_one_does_not_match_doner", test_misfire_4_one_does_not_match_doner),
        ("test_misfire_5_guven_does_not_match_guvenlik", test_misfire_5_guven_does_not_match_guvenlik),
        ("test_positive_strong_match_still_works", test_positive_strong_match_still_works),
        ("test_positive_two_term_match_passes_threshold", test_positive_two_term_match_passes_threshold),
        ("test_single_term_brand_query_rejected_under_new_strict_policy", test_single_term_brand_query_rejected_under_new_strict_policy),
        ("test_decision_tree_pronoun_inheritance_not_overridden_by_weak_match", test_decision_tree_pronoun_inheritance_not_overridden_by_weak_match),
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
