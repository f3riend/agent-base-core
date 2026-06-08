"""Erişim kontrolü — kullanıcının hangi mağazaları görebileceğini hesaplar.

Şu an users tablosunda is_admin/role yok — user_id=1 admin sayılıyor.
İlerde users tablosuna is_admin BOOLEAN eklenirse burası DB lookup'a çevrilebilir.
"""
from __future__ import annotations

import os

from sqlalchemy import text


_ADMIN_USER_IDS_ENV = os.environ.get("ADMIN_USER_IDS", "1")
_ADMIN_IDS: set[int] = set()
for tok in _ADMIN_USER_IDS_ENV.split(","):
    tok = tok.strip()
    if tok.isdigit():
        _ADMIN_IDS.add(int(tok))


def is_admin_user(user_id: int) -> bool:
    """Şu an env var üzerinden; users.is_admin eklenirse DB lookup'a çevir."""
    try:
        return int(user_id) in _ADMIN_IDS
    except (TypeError, ValueError):
        return False


def get_user_store_ids(user_id: int) -> list[str]:
    """Kullanıcının sahip olduğu tüm store UUID'lerini döner (str listesi)."""
    try:
        from app.core.database import SessionLocal

        with SessionLocal() as session:
            rows = session.execute(
                text("SELECT id::text AS id FROM stores WHERE user_id = :uid"),
                {"uid": int(user_id)},
            ).all()
            return [r.id for r in rows]
    except Exception as exc:
        print(f"[ACCESS] get_user_store_ids failed: {exc}")
        return []


def _resolve_store_by_slug(slug: str) -> str | None:
    """Slug → store_id. Slug = mağaza adının küçük harfli, boşluksuz hali."""
    if not slug:
        return None
    try:
        from app.core.database import SessionLocal

        with SessionLocal() as session:
            row = session.execute(
                text(
                    "SELECT id::text AS id FROM stores "
                    "WHERE LOWER(REPLACE(name, ' ', '')) LIKE :pat "
                    "ORDER BY created_at DESC LIMIT 1"
                ),
                {"pat": f"%{slug.lower()}%"},
            ).first()
            return row.id if row else None
    except Exception as exc:
        print(f"[ACCESS] _resolve_store_by_slug failed: {exc}")
        return None


def _resolve_user_id_by_slug(slug: str) -> int | None:
    if not slug:
        return None
    try:
        from app.core.database import SessionLocal

        with SessionLocal() as session:
            row = session.execute(
                text("SELECT id FROM users WHERE LOWER(username) = :u LIMIT 1"),
                {"u": slug.lower()},
            ).first()
            return int(row.id) if row else None
    except Exception as exc:
        print(f"[ACCESS] _resolve_user_id_by_slug failed: {exc}")
        return None


def resolve_scope_to_store_ids(
    mention,
    user_id: int,
    is_admin: bool,
) -> list[str]:
    """MentionContext + user → erişilebilir store_id listesi.

    scope="self"  → kullanıcının tüm mağazaları
    scope="store" → slug eşleşmesi, sahibi değilse boş liste
    scope="user"  → sadece admin: hedef kullanıcının mağazaları
    scope="all"   → admin: tüm mağazalar; satıcı: kendi mağazaları
    """
    scope = getattr(mention, "scope", "self")

    if scope == "store":
        sid = _resolve_store_by_slug(mention.store_slug or "")
        if not sid:
            return []
        if is_admin:
            return [sid]
        owned = set(get_user_store_ids(user_id))
        return [sid] if sid in owned else []

    if scope == "user":
        if not is_admin:
            return get_user_store_ids(user_id)
        target_uid = _resolve_user_id_by_slug(mention.user_slug or "")
        if not target_uid:
            return []
        return get_user_store_ids(target_uid)

    if scope == "all":
        if is_admin:
            try:
                from app.core.database import SessionLocal

                with SessionLocal() as session:
                    rows = session.execute(
                        text("SELECT id::text AS id FROM stores")
                    ).all()
                    return [r.id for r in rows]
            except Exception as exc:
                print(f"[ACCESS] all-stores failed: {exc}")
                return []
        return get_user_store_ids(user_id)

    return get_user_store_ids(user_id)
