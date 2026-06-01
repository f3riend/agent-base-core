"""
Safety layer — rate limits, cooldowns, duplicate prevention, autonomous quotas.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

from db import db_connection, execute_query, now_iso
from observability import _emit


AUTONOMOUS_HOURLY_LIMIT = int(os.environ.get("AUTONOMOUS_HOURLY_LIMIT", "30"))
CAMPAIGN_DAILY_LIMIT = int(os.environ.get("CAMPAIGN_DAILY_LIMIT", "15"))
COOLDOWN_MINUTES = int(os.environ.get("AUTONOMOUS_COOLDOWN_MINUTES", "5"))


def init_safety_tables():
    with db_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS safety_counters (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id             INTEGER NOT NULL,
                counter_key         TEXT NOT NULL,
                count_value         INTEGER DEFAULT 0,
                window_start        TEXT NOT NULL,
                updated_at          TEXT NOT NULL,
                UNIQUE(user_id, counter_key)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS execution_cooldowns (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id             INTEGER NOT NULL,
                entity_type         TEXT,
                entity_id           INTEGER,
                workflow_name       TEXT,
                last_executed_at    TEXT NOT NULL,
                UNIQUE(user_id, entity_type, entity_id, workflow_name)
            )
        """)


def _increment_counter(user_id: int, key: str) -> int:
    init_safety_tables()
    ts = now_iso()
    window = datetime.utcnow().strftime("%Y-%m-%d-%H")

    with db_connection() as conn:
        row = conn.execute(
            """
            SELECT id, count_value, window_start FROM safety_counters
            WHERE user_id=? AND counter_key=?
            """,
            (user_id, key),
        ).fetchone()

        if row and row["window_start"] == window:
            new_val = row["count_value"] + 1
            conn.execute(
                """
                UPDATE safety_counters
                SET count_value=?, updated_at=?
                WHERE id=?
                """,
                (new_val, ts, row["id"]),
            )
            return new_val

        conn.execute(
            """
            INSERT INTO safety_counters (user_id, counter_key, count_value, window_start, updated_at)
            VALUES (?, ?, 1, ?, ?)
            ON CONFLICT(user_id, counter_key) DO UPDATE SET
                count_value=1, window_start=excluded.window_start, updated_at=excluded.updated_at
            """,
            (user_id, key, window, ts),
        )
        return 1


def _get_counter(user_id: int, key: str) -> int:
    init_safety_tables()
    window = datetime.utcnow().strftime("%Y-%m-%d-%H")
    row = execute_query(
        """
        SELECT count_value FROM safety_counters
        WHERE user_id=? AND counter_key=? AND window_start=?
        """,
        (user_id, key, window),
        one=True,
    )
    return row["count_value"] if row else 0


def check_autonomous_quota(user_id: int) -> tuple[bool, str]:
    count = _get_counter(user_id, "autonomous_hourly")
    if count >= AUTONOMOUS_HOURLY_LIMIT:
        return False, f"Saatlik otonom limit aşıldı ({count}/{AUTONOMOUS_HOURLY_LIMIT})"
    return True, "ok"


def check_campaign_spam(user_id: int, workflow_name: str) -> tuple[bool, str]:
    if "campaign" not in workflow_name and "instagram" not in workflow_name:
        return True, "ok"
    day_key = f"campaign_daily_{datetime.utcnow().strftime('%Y-%m-%d')}"
    count = _get_counter(user_id, day_key)
    if count >= CAMPAIGN_DAILY_LIMIT:
        return False, f"Günlük kampanya limiti ({CAMPAIGN_DAILY_LIMIT})"
    return True, "ok"


def check_cooldown(
    user_id: int,
    entity_type: str,
    entity_id: int,
    workflow_name: str,
) -> tuple[bool, str]:
    init_safety_tables()
    row = execute_query(
        """
        SELECT last_executed_at FROM execution_cooldowns
        WHERE user_id=? AND entity_type=? AND entity_id=? AND workflow_name=?
        """,
        (user_id, entity_type, entity_id, workflow_name),
        one=True,
    )
    if not row:
        return True, "ok"

    last = datetime.fromisoformat(row["last_executed_at"])
    if datetime.utcnow() - last < timedelta(minutes=COOLDOWN_MINUTES):
        return False, f"Soğuma süresi aktif ({COOLDOWN_MINUTES} dk)"
    return True, "ok"


def record_execution(
    user_id: int,
    entity_type: str,
    entity_id: int,
    workflow_name: str,
    is_campaign: bool = False,
):
    init_safety_tables()
    ts = now_iso()
    _increment_counter(user_id, "autonomous_hourly")
    if is_campaign:
        day_key = f"campaign_daily_{datetime.utcnow().strftime('%Y-%m-%d')}"
        _increment_counter(user_id, day_key)

    with db_connection() as conn:
        conn.execute(
            """
            INSERT INTO execution_cooldowns (
                user_id, entity_type, entity_id, workflow_name, last_executed_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, entity_type, entity_id, workflow_name)
            DO UPDATE SET last_executed_at=excluded.last_executed_at
            """,
            (user_id, entity_type, entity_id, workflow_name, ts),
        )


def validate_autonomous_execution(
    user_id: int,
    entity_type: str,
    entity_id: int,
    workflow_name: str,
) -> tuple[bool, str]:
    checks = [
        check_autonomous_quota(user_id),
        check_campaign_spam(user_id, workflow_name),
        check_cooldown(user_id, entity_type, entity_id, workflow_name),
    ]
    for ok, msg in checks:
        if not ok:
            _emit("SAFETY_BLOCK", {
                "user_id": user_id,
                "workflow": workflow_name,
                "reason": msg,
            })
            return False, msg
    return True, "ok"
