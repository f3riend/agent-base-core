"""
AI-assisted customer interaction service.

The runtime already has SupportResponseTool and a sentiment detector in
business_intelligence. What was missing: the **thread**. A real customer
interaction is a sequence of messages with state (open / awaiting human /
resolved / escalated), sentiment trajectory, and an audit trail of who
said what.

This module owns:
    - customer_threads — per-channel thread state.
    - customer_messages — every customer / AI-draft / operator / system
      message in chronological order.
    - draft_response — produces an AI draft for the latest customer message.
      Routes through approval_service for risky drafts (negative sentiment,
      legal/refund mentions, escalation keywords).
    - escalate — flips status to escalated and emits an approval request
      with risk_level="high".

This is in-process only. Real channel integration (Instagram DM, email,
Trendyol Q&A) is Phase D of the evolution plan — when a `channel_adapters/`
package lands, it will feed `ingest_message()` from the outside.

Design notes:
    - The detector decides "risky vs safe" deterministically. AI does not
      decide whether its own output requires approval.
    - Approval requests created here re-use the existing approval pipeline
      (planner_runtime.apply_approved_proposal compatible shape).
    - Every state change emits an orchestration_traces row through
      observability._emit(persist=True).
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Optional

from db import DEFAULT_USER_ID, db_connection, execute_query, execute_write, now_iso
from observability import _emit


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


THREAD_OPEN      = "open"
THREAD_AWAITING  = "awaiting_human"
THREAD_ESCALATED = "escalated"
THREAD_RESOLVED  = "resolved"

VALID_CHANNELS = (
    "instagram_dm", "facebook_dm", "email",
    "trendyol_qa", "shopify_msg", "chat", "inline",
)

VALID_ROLES = ("customer", "ai_draft", "operator", "system")


def init_customer_tables() -> None:
    with db_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS customer_threads (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL,
                channel         TEXT NOT NULL,
                customer_ref    TEXT NOT NULL,
                customer_name   TEXT,
                topic           TEXT,
                status          TEXT DEFAULT 'open',
                sentiment       TEXT DEFAULT 'neutral',
                sentiment_score REAL DEFAULT 0,
                risk_flags_json TEXT,
                last_message_at TEXT,
                opened_at       TEXT NOT NULL,
                closed_at       TEXT,
                meta_json       TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_threads_user_status "
            "ON customer_threads (user_id, status, last_message_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_threads_channel "
            "ON customer_threads (channel, customer_ref)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS customer_messages (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id       INTEGER NOT NULL,
                from_role       TEXT NOT NULL,
                text            TEXT NOT NULL,
                sentiment       TEXT,
                sentiment_score REAL,
                approval_id     INTEGER,
                created_at      TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_msgs_thread "
            "ON customer_messages (thread_id, id)"
        )


init_customer_tables()


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


_NEGATIVE_TOKENS = {
    "kötü", "berbat", "iğrenç", "rezalet", "memnun değil", "memnun değilim",
    "kandırıldım", "dolandırıcı", "iade", "para iadesi", "şikayet", "şikayetçi",
    "iptal", "geç", "bozuk", "hatalı", "yanlış", "kırık", "kayıp", "ulaşmadı",
    "horrible", "bad", "refund", "scam", "lost", "broken", "missing", "delay",
    "wrong", "complaint", "angry", "cancel",
}

_POSITIVE_TOKENS = {
    "harika", "mükemmel", "süper", "çok iyi", "bayıldım", "teşekkür", "teşekkürler",
    "mutlu", "great", "amazing", "perfect", "love", "thanks", "thank you",
}

_RISK_TRIGGERS = {
    # legal / financial — never send without human review
    "iade", "geri ödeme", "tazminat", "dava", "mahkeme", "yasal", "avukat",
    "refund", "lawsuit", "legal", "compensation",
    # escalation phrasing
    "müdür", "yönetici", "şikayet edeceğim", "sosyal medya", "viral",
    "manager", "escalate", "press", "media", "report you",
}

_GREETING_TOKENS = {"merhaba", "selam", "iyi günler", "hi", "hello", "hey"}


def detect_sentiment(text: str) -> tuple[str, float]:
    """Naive Turkish/English sentiment classifier — score in [-1, 1]."""
    if not text:
        return "neutral", 0.0
    t = text.lower()
    neg = sum(1 for tok in _NEGATIVE_TOKENS if tok in t)
    pos = sum(1 for tok in _POSITIVE_TOKENS if tok in t)
    if neg == 0 and pos == 0:
        return "neutral", 0.0
    score = (pos - neg) / max(1, pos + neg)
    if score <= -0.4:
        return "negative", round(score, 2)
    if score >= 0.4:
        return "positive", round(score, 2)
    return "neutral", round(score, 2)


def detect_risk_flags(text: str) -> list[str]:
    """Return triggered risk tokens. Non-empty result → approval required."""
    if not text:
        return []
    t = text.lower()
    flags: list[str] = []
    for tok in _RISK_TRIGGERS:
        if tok in t:
            flags.append(tok)
    return sorted(set(flags))


# ---------------------------------------------------------------------------
# Thread CRUD
# ---------------------------------------------------------------------------


def _row_to_thread(row: sqlite3.Row | dict) -> dict:
    d = dict(row)
    raw_meta = d.pop("meta_json", None) or "{}"
    raw_flags = d.pop("risk_flags_json", None) or "[]"
    try:
        d["meta"] = json.loads(raw_meta)
    except json.JSONDecodeError:
        d["meta"] = {}
    try:
        d["risk_flags"] = json.loads(raw_flags)
    except json.JSONDecodeError:
        d["risk_flags"] = []
    return d


def _row_to_message(row: sqlite3.Row | dict) -> dict:
    return dict(row)


def _update_thread_from_message(thread_id: int, text: str, role: str) -> None:
    """Refresh denormalized fields on the thread row after a new message."""
    sentiment, score = detect_sentiment(text) if role == "customer" else (None, None)
    flags = detect_risk_flags(text) if role == "customer" else []
    ts = now_iso()
    if sentiment is not None:
        execute_write(
            """
            UPDATE customer_threads
            SET sentiment=?, sentiment_score=?,
                risk_flags_json=?, last_message_at=?
            WHERE id=?
            """,
            (sentiment, score, json.dumps(flags), ts, int(thread_id)),
        )
    else:
        execute_write(
            "UPDATE customer_threads SET last_message_at=? WHERE id=?",
            (ts, int(thread_id)),
        )


def open_thread(
    *,
    user_id: int,
    channel: str,
    customer_ref: str,
    initial_message: str,
    customer_name: str | None = None,
    topic: str | None = None,
    meta: dict | None = None,
) -> dict:
    """Create a new customer thread and ingest the first message."""
    if channel not in VALID_CHANNELS:
        raise ValueError(
            f"unsupported channel {channel!r}; expected one of {VALID_CHANNELS}"
        )
    ts = now_iso()
    thread_id = execute_write(
        """
        INSERT INTO customer_threads (
            user_id, channel, customer_ref, customer_name, topic,
            status, sentiment, sentiment_score, risk_flags_json,
            last_message_at, opened_at, meta_json
        )
        VALUES (?, ?, ?, ?, ?, 'open', 'neutral', 0, '[]', ?, ?, ?)
        """,
        (
            int(user_id), channel, customer_ref, customer_name, topic,
            ts, ts, json.dumps(meta or {}, default=str, ensure_ascii=False),
        ),
    )

    ingest_message(thread_id, message=initial_message, from_role="customer")

    _emit(
        "CUSTOMER_THREAD_OPENED",
        {
            "thread_id": thread_id,
            "channel": channel,
            "customer_ref": customer_ref,
            "summary": f"Yeni müşteri konuşması açıldı ({channel})",
        },
        persist=True,
        user_id=int(user_id),
    )
    return get_thread(thread_id)


def ingest_message(
    thread_id: int,
    *,
    message: str,
    from_role: str = "customer",
    approval_id: int | None = None,
) -> dict:
    """Append a message to a thread; update denormalized state."""
    if from_role not in VALID_ROLES:
        raise ValueError(f"invalid from_role {from_role!r}")
    thread = get_thread(thread_id)
    if not thread:
        raise ValueError(f"thread not found: {thread_id}")

    sentiment, score = (None, None)
    if from_role == "customer":
        sentiment, score = detect_sentiment(message)

    new_id = execute_write(
        """
        INSERT INTO customer_messages (
            thread_id, from_role, text, sentiment, sentiment_score,
            approval_id, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (int(thread_id), from_role, message, sentiment, score, approval_id, now_iso()),
    )
    _update_thread_from_message(thread_id, message, from_role)

    if from_role == "operator":
        # Operator answered → if thread was awaiting_human, mark resolved.
        if thread["status"] == THREAD_AWAITING:
            execute_write(
                "UPDATE customer_threads SET status=?, closed_at=? WHERE id=?",
                (THREAD_RESOLVED, now_iso(), int(thread_id)),
            )

    return get_message(new_id) or {}


def get_thread(thread_id: int) -> dict | None:
    row = execute_query(
        "SELECT * FROM customer_threads WHERE id=?",
        (int(thread_id),),
        one=True,
    )
    if not row:
        return None
    return _row_to_thread(row)


def get_message(message_id: int) -> dict | None:
    row = execute_query(
        "SELECT * FROM customer_messages WHERE id=?",
        (int(message_id),),
        one=True,
    )
    return _row_to_message(row) if row else None


def list_messages(thread_id: int, limit: int = 50) -> list[dict]:
    rows = execute_query(
        """
        SELECT * FROM customer_messages
        WHERE thread_id=?
        ORDER BY id ASC LIMIT ?
        """,
        (int(thread_id), int(limit)),
    )
    return [_row_to_message(r) for r in rows]


def list_threads(
    *,
    user_id: int,
    status: str | None = None,
    channel: str | None = None,
    limit: int = 50,
) -> list[dict]:
    sql = "SELECT * FROM customer_threads WHERE user_id=?"
    params: list[Any] = [int(user_id)]
    if status:
        sql += " AND status=?"
        params.append(status)
    if channel:
        sql += " AND channel=?"
        params.append(channel)
    sql += " ORDER BY last_message_at DESC NULLS LAST LIMIT ?"
    # SQLite < 3.30 doesn't support NULLS LAST consistently; safe fallback:
    sql = sql.replace("NULLS LAST", "")
    params.append(int(limit))
    rows = execute_query(sql, tuple(params))
    return [_row_to_thread(r) for r in rows]


def mark_resolved(thread_id: int, *, reason: str = "resolved") -> dict:
    thread = get_thread(thread_id)
    if not thread:
        raise ValueError(f"thread not found: {thread_id}")
    if thread["status"] == THREAD_RESOLVED:
        return thread
    execute_write(
        """
        UPDATE customer_threads
        SET status=?, closed_at=?
        WHERE id=?
        """,
        (THREAD_RESOLVED, now_iso(), int(thread_id)),
    )
    ingest_message(thread_id, message=f"[system] resolved: {reason}", from_role="system")
    _emit(
        "CUSTOMER_THREAD_RESOLVED",
        {"thread_id": thread_id, "reason": reason},
        persist=True,
        user_id=thread["user_id"],
    )
    return get_thread(thread_id) or thread


# ---------------------------------------------------------------------------
# Drafting
# ---------------------------------------------------------------------------


_TONE_BY_SENTIMENT = {
    "negative": "apologetic",
    "neutral":  "friendly",
    "positive": "friendly",
}


def _last_customer_message(thread_id: int) -> dict | None:
    rows = execute_query(
        """
        SELECT * FROM customer_messages
        WHERE thread_id=? AND from_role='customer'
        ORDER BY id DESC LIMIT 1
        """,
        (int(thread_id),),
    )
    return _row_to_message(rows[0]) if rows else None


def _compose_draft(thread: dict, last_msg: dict) -> str:
    """Compose a deterministic Turkish draft reply.

    This is intentionally simple — the real product call site can switch
    to an LLM by reading thread context + last_msg.text. Determinism here
    keeps the test runtime predictable.
    """
    sentiment = (last_msg.get("sentiment") or thread.get("sentiment") or "neutral")
    customer_name = thread.get("customer_name") or "Merhaba"
    text = last_msg.get("text", "")

    if sentiment == "negative":
        return (
            f"{customer_name}, yaşadığın deneyim için gerçekten üzgünüm. "
            f"Konuyu hemen incelemeye alıyoruz; en kısa sürede sana detaylı "
            f"bir geri dönüş yapacağım. Bu arada özel bir not eklemek "
            f"istersen, bana yazmaktan çekinme."
        )
    if sentiment == "positive":
        return (
            f"{customer_name}, harika geri bildirimin için çok teşekkür ederim! "
            f"Yorumun bizim için motive edici — bu enerjiyle devam edeceğiz. "
            f"Sana özel bir hatırlatma veya öneri istersen söyle, hemen ileteyim."
        )
    return (
        f"{customer_name}, mesajın için teşekkürler. Sorunu inceledim — "
        f"ihtiyacın olan bilgiyi en doğru şekilde iletmek için bir saniye "
        f"bekler misin? Hemen dönüş yapacağım."
    )


def draft_response(
    thread_id: int,
    *,
    create_approval: bool = True,
) -> dict:
    """Produce an AI draft for the latest customer message in the thread.

    If risk flags are detected on the latest customer message OR sentiment
    is negative, an approval request is created (risk_level='high') and the
    thread is moved to status=awaiting_human. Otherwise the draft is just
    persisted as an `ai_draft` message awaiting operator review.

    Returns the draft message row plus the optional approval_id.
    """
    thread = get_thread(thread_id)
    if not thread:
        raise ValueError(f"thread not found: {thread_id}")
    last = _last_customer_message(thread_id)
    if not last:
        raise ValueError("no customer messages to draft a response to")

    risk_flags = detect_risk_flags(last["text"])
    sentiment = thread.get("sentiment", "neutral")
    needs_approval = bool(risk_flags) or sentiment == "negative"

    draft_text = _compose_draft(thread, last)
    approval_id: int | None = None

    if create_approval and needs_approval:
        from approval_service import create_approval_request

        proposal = {
            "decision": "create_workflow",
            "workflow_name": f"customer_reply_{thread['channel']}_{thread_id}",
            "reason": (
                f"Müşteri konuşması #{thread_id} — taslak yanıt için onay isteniyor"
                + (f" (risk: {', '.join(risk_flags)})" if risk_flags else "")
            ),
            "tools": ["support_response_tool"],
            "priority": "high",
            "confidence": 0.7,
            "requires_approval": True,
            "business_intent": "customer_support",
            "task_payload": {
                "thread_id": thread_id,
                "customer_ref": thread["customer_ref"],
                "channel": thread["channel"],
                "customer_question": last["text"],
                "draft_reply": draft_text,
                "tone": _TONE_BY_SENTIMENT.get(sentiment, "friendly"),
            },
            "entity_type": "thread",
            "entity_id": thread_id,
        }
        approval_id = create_approval_request(
            user_id=thread["user_id"],
            proposal=proposal,
            event_id=None,
        )

        execute_write(
            "UPDATE customer_threads SET status=? WHERE id=?",
            (THREAD_AWAITING, int(thread_id)),
        )

    msg = ingest_message(
        thread_id,
        message=draft_text,
        from_role="ai_draft",
        approval_id=approval_id,
    )

    _emit(
        "CUSTOMER_DRAFT_CREATED",
        {
            "thread_id": thread_id,
            "message_id": msg.get("id"),
            "approval_id": approval_id,
            "needs_approval": needs_approval,
            "risk_flags": risk_flags,
            "summary": (
                f"AI yanıtı taslağı hazır — "
                + ("insan onayı bekliyor" if needs_approval else "operatör inceliyor")
            ),
        },
        persist=True,
        user_id=thread["user_id"],
    )

    return {
        "thread_id": thread_id,
        "draft_message": msg,
        "draft_text": draft_text,
        "needs_approval": needs_approval,
        "approval_id": approval_id,
        "risk_flags": risk_flags,
        "sentiment": sentiment,
    }


def escalate(
    thread_id: int,
    *,
    reason: str,
    level: str = "manager",
) -> dict:
    """Flip thread to escalated; raise an approval request for visibility."""
    from approval_service import create_approval_request

    thread = get_thread(thread_id)
    if not thread:
        raise ValueError(f"thread not found: {thread_id}")

    execute_write(
        "UPDATE customer_threads SET status=? WHERE id=?",
        (THREAD_ESCALATED, int(thread_id)),
    )

    proposal = {
        "decision": "noop",
        "workflow_name": f"customer_escalation_{thread['channel']}_{thread_id}",
        "reason": f"Müşteri konuşması eskale edildi: {reason}",
        "tools": [],
        "priority": "high",
        "confidence": 0.9,
        "requires_approval": True,
        "business_intent": "customer_support",
        "task_payload": {
            "thread_id": thread_id,
            "level": level,
            "customer_ref": thread["customer_ref"],
            "channel": thread["channel"],
            "reason": reason,
        },
        "entity_type": "thread",
        "entity_id": thread_id,
    }
    approval_id = create_approval_request(
        user_id=thread["user_id"], proposal=proposal, event_id=None,
    )

    ingest_message(
        thread_id,
        message=f"[system] eskalasyon ({level}): {reason}",
        from_role="system",
        approval_id=approval_id,
    )

    _emit(
        "CUSTOMER_THREAD_ESCALATED",
        {
            "thread_id": thread_id,
            "level": level,
            "reason": reason,
            "approval_id": approval_id,
            "summary": f"Müşteri konuşması eskale edildi → {level}",
        },
        persist=True,
        user_id=thread["user_id"],
    )

    return {
        "thread": get_thread(thread_id),
        "approval_id": approval_id,
    }


# ---------------------------------------------------------------------------
# Read API for the dashboard
# ---------------------------------------------------------------------------


def thread_summary(thread_id: int) -> dict | None:
    """Thread + last 20 messages — primary dashboard view payload."""
    thread = get_thread(thread_id)
    if not thread:
        return None
    return {
        "thread": thread,
        "messages": list_messages(thread_id, limit=50),
    }


def threads_overview(user_id: int = DEFAULT_USER_ID, limit: int = 30) -> dict:
    threads = list_threads(user_id=user_id, limit=limit)
    counts = {"open": 0, "awaiting_human": 0, "escalated": 0, "resolved": 0}
    for t in threads:
        if t["status"] in counts:
            counts[t["status"]] += 1
    return {"threads": threads, "counts": counts}
