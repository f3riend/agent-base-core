"""
Direct timeline access — NO HTTP self-calls.

Reads from fake_ai_api.db (same source as main.py timeline API).
Used by orchestration_api, listener, and resource_service.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Optional

FAKE_API_DB_PATH = os.environ.get("FAKE_API_DB_PATH", "fake_ai_api.db")


@contextmanager
def _timeline_db():
    conn = sqlite3.connect(FAKE_API_DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _row_to_event(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "ts": row["ts"],
        "event": row["event"],
        "event_label": row["event_label"],
        "group": row["log_group"],
        "group_label": row["group_label"],
        "description": row["description"],
        "store_id": row["store_id"],
        "subject": {
            "type": row["subject_type"],
            "id": row["subject_id"],
        },
        "causer": {
            "type": row["causer_type"],
            "id": row["causer_id"],
            "name": row["causer_name"],
        },
        "changes": json.loads(row["changes"] or "{}"),
        "payload": json.loads(row["payload"] or "{}"),
        "meta": json.loads(row["meta"] or "{}"),
    }


def fetch_timeline(
    cursor: int = 0,
    direction: str = "desc",
    limit: int = 50,
    log_group: Optional[str] = None,
    event: Optional[str] = None,
    store_id: Optional[int] = None,
    user_id: Optional[int] = None,
) -> dict[str, Any]:
    """
    Fetch timeline events directly from DB.
    Returns API-compatible shape: { data, pagination }.
    """
    started = time.monotonic()

    query = "SELECT * FROM timeline WHERE 1=1"
    params: list[Any] = []

    if cursor:
        if direction == "asc":
            query += " AND id > ?"
        else:
            query += " AND id < ?"
        params.append(cursor)

    if log_group:
        query += " AND log_group=?"
        params.append(log_group)

    if event:
        query += " AND event=?"
        params.append(event)

    if store_id:
        query += " AND store_id=?"
        params.append(store_id)

    if user_id is not None:
        store_rows = []
        with _timeline_db() as conn:
            try:
                store_rows = conn.execute(
                    "SELECT id FROM stores WHERE user_id=?",
                    (user_id,),
                ).fetchall()
            except sqlite3.OperationalError:
                pass

        if store_rows:
            ids = [r["id"] for r in store_rows]
            placeholders = ",".join("?" * len(ids))
            query += f" AND store_id IN ({placeholders})"
            params.extend(ids)

    order = "ASC" if direction == "asc" else "DESC"
    query += f" ORDER BY id {order} LIMIT ?"
    params.append(limit)

    with _timeline_db() as conn:
        rows = conn.execute(query, params).fetchall()

    events = [_row_to_event(row) for row in rows]

    elapsed = int((time.monotonic() - started) * 1000)
    print(
        f"[METRIC] timeline_db_fetch_ms={elapsed} "
        f"events={len(events)} cursor={cursor} direction={direction}"
    )

    next_cursor = events[-1]["id"] if events else None

    return {
        "data": events,
        "pagination": {
            "count": len(events),
            "limit": limit,
            "direction": direction,
            "next_cursor": next_cursor,
            "has_more": len(events) >= limit,
        },
    }


def fetch_events_after_cursor(cursor: int = 0, limit: int = 100) -> list[dict]:
    """Listener polling — ascending events after cursor."""
    result = fetch_timeline(
        cursor=cursor,
        direction="asc",
        limit=limit,
    )
    return result["data"]


def get_event_by_id(event_id: int) -> Optional[dict]:
    with _timeline_db() as conn:
        row = conn.execute(
            "SELECT * FROM timeline WHERE id=?",
            (event_id,),
        ).fetchone()
    return _row_to_event(row) if row else None


def get_latest_events(limit: int = 20, user_id: Optional[int] = None) -> list[dict]:
    result = fetch_timeline(
        cursor=0,
        direction="desc",
        limit=limit,
        user_id=user_id,
    )
    return result["data"]
