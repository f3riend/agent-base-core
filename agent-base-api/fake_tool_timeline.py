"""Emit timeline events from fake tool executions.

These events are envelope-tagged with source="tool_emit" so the listener can
recognize them as synthetic echoes. The BI layer still consumes them — they
exist precisely so tools' downstream effects are observable — but the listener
will not treat a tool_emit event as a primary trigger for further planning.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime

FAKE_API_DB_PATH = os.environ.get("FAKE_API_DB_PATH", "fake_ai_api.db")


def _conn():
    c = sqlite3.connect(FAKE_API_DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def emit_tool_event(
    tool_name: str,
    description: str,
    log_group: str = "automation",
    event: str = "executed",
    store_id: int | None = None,
    subject_type: str | None = None,
    subject_id: int | None = None,
    payload: dict | None = None,
    *,
    causation_event_id: int | None = None,
    correlation_id: str | None = None,
    tenant_id: int | None = None,
) -> int | None:
    try:
        ts = datetime.utcnow().isoformat()
        meta = {
            "processed_by_rule_engine": True,
            "orchestration": {
                "path": "tool_execution",
                "route": "tool",
                "processed_at": ts,
            },
            "tool_name": tool_name,
            # Envelope-style tagging — the listener and BI layer read these.
            "source": "tool_emit",
            "priority": "background",
            "causation_id": causation_event_id,
            "correlation_id": correlation_id,
            "tenant_id": tenant_id,
        }
        conn = _conn()
        cur = conn.execute(
            """
            INSERT INTO timeline (
                ts, event, event_label, log_group, group_label,
                description, store_id, subject_type, subject_id,
                causer_type, causer_id, causer_name,
                changes, payload, meta
            )
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                ts, event, event.title(), log_group, log_group.title(),
                description, store_id, subject_type, subject_id,
                "tool", 0, tool_name,
                "{}",
                json.dumps(payload or {}, ensure_ascii=False),
                json.dumps(meta, ensure_ascii=False),
            ),
        )
        eid = cur.lastrowid
        conn.commit()
        conn.close()
        print(f"[TOOL_TIMELINE] {tool_name}: {description} (#{eid})")
        return eid
    except Exception as exc:
        print(f"[TOOL_TIMELINE] skip: {exc}")
        return None
