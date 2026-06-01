"""
Timeline event processing metadata — processed_by_rule_engine fix.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime

FAKE_API_DB_PATH = os.environ.get("FAKE_API_DB_PATH", "fake_ai_api.db")


def now_iso():
    return datetime.utcnow().isoformat()


def _get_conn():
    conn = sqlite3.connect(FAKE_API_DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def update_event_processing_meta(
    event_id: int,
    *,
    processed: bool = True,
    route: str = "",
    path: str = "",
    rules_matched: int = 0,
    planner_used: bool = False,
    autonomous_used: bool = False,
    skip_reason: str | None = None,
    listener_ms: int = 0,
):
    """Update timeline.meta after listener processes an event."""
    meta = {
        "processed_by_rule_engine": processed,
        "orchestration": {
            "route": route,
            "path": path,
            "rules_matched": rules_matched,
            "planner_used": planner_used,
            "autonomous_used": autonomous_used,
            "skip_reason": skip_reason,
            "processed_at": now_iso(),
            "listener_ms": listener_ms,
        },
    }

    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT meta FROM timeline WHERE id=?",
            (event_id,),
        ).fetchone()
        if row and row["meta"]:
            try:
                existing = json.loads(row["meta"])
                existing.update(meta)
                meta = existing
            except json.JSONDecodeError:
                pass

        conn.execute(
            "UPDATE timeline SET meta=? WHERE id=?",
            (json.dumps(meta, ensure_ascii=False), event_id),
        )
        conn.commit()
    finally:
        conn.close()

    status = "işlendi" if processed else "atlandı"
    reason = skip_reason or path or route
    print(
        f"[TIMELINE_META] event #{event_id} {status} "
        f"route={route} path={path} rules={rules_matched} "
        f"autonomous={autonomous_used} reason={reason or '-'}"
    )


def mark_event_pending(event_id: int):
    """Initial state when event created (before listener)."""
    update_event_processing_meta(
        event_id,
        processed=False,
        path="pending",
        skip_reason="listener bekleniyor",
    )
