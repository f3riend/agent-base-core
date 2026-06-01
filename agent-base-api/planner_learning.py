"""
Lightweight learning — adapt confidence from outcomes over time.
"""

from __future__ import annotations

import json

from db import db_connection, execute_query, now_iso


def init_learning_tables():
    with db_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS planner_learning_stats (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id             INTEGER NOT NULL,
                stat_key            TEXT NOT NULL,
                stat_value          REAL DEFAULT 0,
                count               INTEGER DEFAULT 0,
                meta_json           TEXT,
                updated_at          TEXT NOT NULL,
                UNIQUE(user_id, stat_key)
            )
        """)


def record_outcome(
    user_id: int,
    stat_key: str,
    success: bool,
    meta: dict | None = None,
):
    init_learning_tables()
    delta = 1.0 if success else -0.5
    ts = now_iso()
    with db_connection() as conn:
        row = conn.execute(
            "SELECT stat_value, count FROM planner_learning_stats WHERE user_id=? AND stat_key=?",
            (user_id, stat_key),
        ).fetchone()
        if row:
            new_val = row["stat_value"] + delta
            new_count = row["count"] + 1
            conn.execute(
                """
                UPDATE planner_learning_stats
                SET stat_value=?, count=?, meta_json=?, updated_at=?
                WHERE user_id=? AND stat_key=?
                """,
                (new_val, new_count, json.dumps(meta or {}), ts, user_id, stat_key),
            )
        else:
            conn.execute(
                """
                INSERT INTO planner_learning_stats (user_id, stat_key, stat_value, count, meta_json, updated_at)
                VALUES (?, ?, ?, 1, ?, ?)
                """,
                (user_id, stat_key, delta, json.dumps(meta or {}), ts),
            )


def get_confidence_adjustment(user_id: int, business_intent: str, tools: list[str]) -> float:
    """Returns additive confidence delta in [-0.15, 0.15]."""
    init_learning_tables()
    adj = 0.0
    keys = [f"intent:{business_intent}"] + [f"tool:{t}" for t in tools]
    for key in keys:
        row = execute_query(
            "SELECT stat_value, count FROM planner_learning_stats WHERE user_id=? AND stat_key=?",
            (user_id, key),
            one=True,
        )
        if row and row["count"] >= 3:
            rate = row["stat_value"] / row["count"]
            adj += max(-0.05, min(0.05, rate * 0.02))
    return max(-0.15, min(0.15, adj))


def get_learning_summary(user_id: int) -> dict:
    init_learning_tables()
    rows = execute_query(
        "SELECT stat_key, stat_value, count FROM planner_learning_stats WHERE user_id=? ORDER BY count DESC LIMIT 20",
        (user_id,),
    )
    return {
        "stats": [dict(r) for r in rows],
        "top_tools": [r["stat_key"] for r in rows if r["stat_key"].startswith("tool:")][:5],
    }
