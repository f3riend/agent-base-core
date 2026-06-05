"""Chat session/message servisi — yeni MySQL/PG tabloları (UI sidebar için).

NOT: business_chat.py'nin eski SQLite chat_sessions/chat_turns'una dokunmuyoruz.
Bu servis sadece UI'da gösterilecek session listesi + mesaj geçmişini tutar.

Eski session_id (SQLite) `sess_<hex16>` formatında. Yeni session_id (PG) UUID.
İki sistemi senkron tutmak için: yeni UUID'den deterministik `sess_<hex16>`
türetiyoruz (derive_legacy_id). Aynı UUID hem PG'de hem SQLite'da yaşar.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from app.models.chat_message import ChatMessage
from app.models.chat_session import ChatSession


def derive_legacy_id(new_id: uuid.UUID | str) -> str:
    """UUID → eski SQLite chat_sessions için `sess_<hex16>` format."""
    if isinstance(new_id, str):
        new_id = uuid.UUID(new_id)
    return f"sess_{new_id.hex[:16]}"


def _parse_uuid(raw: str | uuid.UUID | None) -> uuid.UUID | None:
    if raw is None:
        return None
    if isinstance(raw, uuid.UUID):
        return raw
    try:
        return uuid.UUID(str(raw))
    except (ValueError, TypeError):
        return None


def ensure_session(
    db: Session,
    *,
    user_id: int,
    session_id: str | uuid.UUID | None,
    first_message: str | None = None,
) -> ChatSession:
    """Mevcut session'ı bul ya da yeni aç.

    `first_message` verilirse ve session yeni yaratılıyorsa, ilk 50 karakter
    title olarak set edilir.
    """
    uid = _parse_uuid(session_id)
    if uid is not None:
        existing = db.scalar(
            select(ChatSession).where(
                ChatSession.id == uid, ChatSession.user_id == user_id
            )
        )
        if existing is not None:
            return existing

    title: str | None = None
    if first_message:
        title = (first_message or "").strip()[:50] or None

    sess = ChatSession(user_id=user_id, title=title)
    db.add(sess)
    db.commit()
    db.refresh(sess)
    return sess


def append_message(
    db: Session,
    *,
    session_id: uuid.UUID,
    role: str,
    content: str,
) -> ChatMessage:
    """Mesajı yaz + session.last_message_at güncelle."""
    msg = ChatMessage(
        session_id=session_id,
        role=role,
        content=content,
    )
    db.add(msg)
    db.execute(
        update(ChatSession)
        .where(ChatSession.id == session_id)
        .values(last_message_at=datetime.now(timezone.utc))
    )
    db.commit()
    db.refresh(msg)
    return msg


def list_sessions(db: Session, user_id: int, limit: int = 200) -> list[ChatSession]:
    return list(
        db.scalars(
            select(ChatSession)
            .where(ChatSession.user_id == user_id)
            .order_by(ChatSession.last_message_at.desc())
            .limit(limit)
        ).all()
    )


def get_session_with_messages(
    db: Session, user_id: int, session_id: uuid.UUID
) -> ChatSession | None:
    return db.scalar(
        select(ChatSession)
        .where(ChatSession.id == session_id, ChatSession.user_id == user_id)
        .options(selectinload(ChatSession.messages))
    )


def delete_session(db: Session, user_id: int, session_id: uuid.UUID) -> bool:
    sess = db.scalar(
        select(ChatSession).where(
            ChatSession.id == session_id, ChatSession.user_id == user_id
        )
    )
    if sess is None:
        return False
    db.delete(sess)
    db.commit()
    return True
