"""
Planner memory — historical decisions with vector-embedding preparation (no external DB).
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from db import db_connection, execute_query, now_iso


def init_planner_memory_table():
    with db_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS planner_memory (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id             INTEGER NOT NULL,
                event_id            INTEGER,
                event_name          TEXT,
                entity_type         TEXT,
                entity_id           INTEGER,
                decision            TEXT,
                workflow_name       TEXT,
                tools_json          TEXT,
                outcome             TEXT DEFAULT 'pending',
                reason              TEXT,
                confidence          REAL,
                priority            TEXT,
                context_snapshot    TEXT,
                plan_json           TEXT,
                summary_text        TEXT,
                reasoning_trace     TEXT,
                embedding_placeholder TEXT,
                feedback            TEXT,
                approval_status     TEXT,
                tags                TEXT,
                created_at          TEXT NOT NULL
            )
        """)
        _ensure_memory_columns(conn)

        # Dedicated outcomes table — clean queries for the learning system.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS planner_outcomes (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id             INTEGER NOT NULL,
                memory_id           INTEGER,
                workflow_id         INTEGER,
                workflow_name       TEXT,
                business_intent     TEXT,
                outcome             TEXT NOT NULL,
                feedback            TEXT,
                measured_at         TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_outcomes_user_intent
            ON planner_outcomes (user_id, business_intent, measured_at DESC)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_outcomes_workflow
            ON planner_outcomes (workflow_id)
        """)


def _ensure_memory_columns(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(planner_memory)").fetchall()}
    migrations = [
        ("summary_text", "TEXT"),
        ("reasoning_trace", "TEXT"),
        ("embedding_placeholder", "TEXT"),
        ("feedback", "TEXT"),
        ("approval_status", "TEXT"),
        ("tags", "TEXT"),
    ]
    for col, typ in migrations:
        if col not in cols:
            conn.execute(f"ALTER TABLE planner_memory ADD COLUMN {col} {typ}")


def _build_summary(plan: dict, event_name: str) -> str:
    parts = [
        plan.get("business_intent", ""),
        plan.get("workflow_name", ""),
        plan.get("reason", "")[:120],
        event_name,
    ]
    return " | ".join(p for p in parts if p)


def _placeholder_embedding(text: str) -> str:
    """Token-frequency vector placeholder for future embedding store."""
    tokens = re.findall(r"[a-z0-9ğüşıöçâ]+", text.lower())
    freq: dict[str, int] = {}
    for t in tokens:
        if len(t) > 2:
            freq[t] = freq.get(t, 0) + 1
    top = sorted(freq.items(), key=lambda x: -x[1])[:32]
    return json.dumps({"v1_tokens": top, "dim": len(top)})


def record_plan(
    user_id: int,
    event: dict,
    event_name: str,
    subject_type: str,
    subject_id: int,
    plan: dict,
    outcome: str = "pending",
    reasoning_trace: str | None = None,
) -> int:
    init_planner_memory_table()
    summary = _build_summary(plan, event_name)
    embed = _placeholder_embedding(summary)
    tags = json.dumps([
        plan.get("business_intent"),
        plan.get("priority"),
        plan.get("source"),
    ])

    with db_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO planner_memory (
                user_id, event_id, event_name, entity_type, entity_id,
                decision, workflow_name, tools_json, outcome, reason,
                confidence, priority, context_snapshot, plan_json,
                summary_text, reasoning_trace, embedding_placeholder,
                approval_status, tags, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                event.get("id"),
                event_name,
                subject_type.lower(),
                subject_id,
                plan.get("decision"),
                plan.get("workflow_name") or plan.get("workflow"),
                json.dumps(plan.get("tools", [])),
                outcome,
                plan.get("reason"),
                float(plan.get("confidence", 0)),
                plan.get("priority", "medium"),
                json.dumps({
                    "event": event_name,
                    "subject": f"{subject_type}#{subject_id}",
                }),
                json.dumps(plan),
                summary,
                reasoning_trace or plan.get("reasoning", ""),
                embed,
                "pending" if plan.get("requires_approval") else "auto",
                tags,
                now_iso(),
            ),
        )
        return cursor.lastrowid


def record_feedback(
    user_id: int,
    approval_id: int,
    status: str,
    feedback: str | None = None,
    proposal: dict | None = None,
):
    init_planner_memory_table()
    ts = now_iso()
    with db_connection() as conn:
        conn.execute(
            """
            INSERT INTO planner_memory (
                user_id, event_id, decision, workflow_name, outcome,
                reason, feedback, approval_status, plan_json, summary_text,
                created_at
            )
            VALUES (?, ?, 'feedback', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                approval_id,
                proposal.get("workflow_name") if proposal else None,
                status,
                f"Onay geri bildirimi: {status}",
                feedback,
                status,
                json.dumps(proposal or {}),
                f"approval_{approval_id}_{status}",
                ts,
            ),
        )


def update_outcome(memory_id: int, outcome: str, note: str | None = None):
    with db_connection() as conn:
        if note:
            row = conn.execute(
                "SELECT plan_json FROM planner_memory WHERE id=?",
                (memory_id,),
            ).fetchone()
            plan = json.loads(row["plan_json"] or "{}") if row else {}
            plan["outcome_note"] = note
            conn.execute(
                "UPDATE planner_memory SET outcome=?, plan_json=? WHERE id=?",
                (outcome, json.dumps(plan), memory_id),
            )
        else:
            conn.execute(
                "UPDATE planner_memory SET outcome=? WHERE id=?",
                (outcome, memory_id),
            )


def get_recent_memory(
    user_id: int,
    limit: int = 15,
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
) -> list[dict]:
    init_planner_memory_table()
    if entity_type and entity_id:
        rows = execute_query(
            """
            SELECT * FROM planner_memory
            WHERE user_id=? AND entity_type=? AND entity_id=?
            ORDER BY id DESC LIMIT ?
            """,
            (user_id, entity_type, entity_id, limit),
        )
    else:
        rows = execute_query(
            """
            SELECT * FROM planner_memory
            WHERE user_id=?
            ORDER BY id DESC LIMIT ?
            """,
            (user_id, limit),
        )
    return [dict(r) for r in rows]


def recall_similar_campaigns(
    query_text: str,
    user_id: int,
    limit: int = 5,
) -> list[dict]:
    """Token-overlap recall, ranked by overlap × outcome weight.

    Outcome-weighted: completed-success memories rank higher than pending
    or failed ones for the same overlap. This is the recall interface that
    will swap to vector retrieval in a later phase — callers should treat
    the return shape as stable.
    """
    init_planner_memory_table()
    query_tokens = set(re.findall(r"[a-z0-9ğüşıöçâ]+", query_text.lower()))
    if not query_tokens:
        return []

    rows = execute_query(
        """
        SELECT * FROM planner_memory
        WHERE user_id=? AND decision='create_workflow'
        ORDER BY id DESC LIMIT 80
        """,
        (user_id,),
    )

    outcome_weight = {
        "success": 1.25,
        "completed": 1.25,
        "completed_success": 1.25,
        "approved": 1.15,
        "auto_applied": 1.0,
        "pending": 0.8,
        "failed": 0.55,
        "rejected": 0.4,
    }

    scored = []
    for row in rows:
        r = dict(row)
        summary = (r.get("summary_text") or r.get("reason") or "").lower()
        tokens = set(re.findall(r"[a-z0-9ğüşıöçâ]+", summary))
        overlap = len(query_tokens & tokens)
        if overlap <= 0:
            continue
        w = outcome_weight.get(r.get("outcome") or "pending", 0.85)
        score = overlap * w
        scored.append((score, r))

    scored.sort(key=lambda x: -x[0])
    return [r for _, r in scored[:limit]]


def record_planner_outcome(
    user_id: int,
    memory_id: int | None,
    workflow_id: int | None,
    outcome: str,
    *,
    workflow_name: str | None = None,
    business_intent: str | None = None,
    feedback: str | None = None,
):
    """Persist an outcome for the learning system to consume.

    `outcome` is one of: approved, rejected, auto_applied, completed_success,
    completed_failed, edited, cancelled. The planner_learning module reads
    from this table; planner_memory.outcome is also kept in sync.
    """
    init_planner_memory_table()
    ts = now_iso()
    with db_connection() as conn:
        conn.execute(
            """
            INSERT INTO planner_outcomes (
                user_id, memory_id, workflow_id, workflow_name,
                business_intent, outcome, feedback, measured_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                memory_id,
                workflow_id,
                workflow_name,
                business_intent,
                outcome,
                feedback,
                ts,
            ),
        )
        if memory_id:
            conn.execute(
                "UPDATE planner_memory SET outcome=? WHERE id=?",
                (outcome, memory_id),
            )

    # Surface to the learning system (success/failure → confidence drift).
    try:
        from planner_learning import record_outcome as _learn

        is_success = outcome in (
            "approved",
            "auto_applied",
            "completed_success",
            "completed",
            "success",
        )
        if business_intent:
            _learn(user_id, f"intent:{business_intent}", is_success)
        # We don't know tools at this layer; planner_outcomes index by intent.
    except Exception as exc:
        print(f"[MEMORY] learning update skipped: {exc}")


def get_successful_campaigns(user_id: int, limit: int = 10) -> list[dict]:
    rows = execute_query(
        """
        SELECT * FROM planner_memory
        WHERE user_id=? AND outcome IN ('success', 'completed')
          AND decision='create_workflow'
        ORDER BY id DESC LIMIT ?
        """,
        (user_id, limit),
    )
    return [dict(r) for r in rows]


def build_memory_context(
    user_id: int,
    entity_type: str,
    entity_id: int,
    query_hint: str = "",
) -> dict:
    recent = get_recent_memory(
        user_id, limit=10, entity_type=entity_type, entity_id=entity_id
    )
    successful = get_successful_campaigns(user_id, limit=5)
    similar = recall_similar_campaigns(query_hint, user_id, limit=3) if query_hint else []

    return {
        "recent_decisions": [
            {
                "workflow": r["workflow_name"],
                "outcome": r["outcome"],
                "reason": r["reason"],
                "confidence": r["confidence"],
                "event": r["event_name"],
                "summary": r.get("summary_text"),
                "approval": r.get("approval_status"),
            }
            for r in recent
        ],
        "successful_campaigns": [
            {
                "workflow": r["workflow_name"],
                "tools": json.loads(r["tools_json"] or "[]"),
                "reason": r["reason"],
                "summary": r.get("summary_text"),
            }
            for r in successful
        ],
        "similar_campaigns": [
            {
                "workflow": r["workflow_name"],
                "reason": r["reason"],
                "confidence": r["confidence"],
            }
            for r in similar
        ],
        "failed_count": sum(1 for r in recent if r["outcome"] == "failed"),
        "pending_count": sum(1 for r in recent if r["outcome"] == "pending"),
        "embedding_ready": True,
    }


def get_memory_summary_for_api(user_id: int, limit: int = 20) -> list[dict]:
    rows = get_recent_memory(user_id, limit=limit)
    return [
        {
            "id": r["id"],
            "workflow": r["workflow_name"],
            "decision": r["decision"],
            "outcome": r["outcome"],
            "confidence": r["confidence"],
            "summary": r.get("summary_text"),
            "feedback": r.get("feedback"),
            "approval_status": r.get("approval_status"),
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def get_automation_log_summary(user_id: int, limit: int = 10) -> list[dict]:
    rows = execute_query(
        """
        SELECT rule_name, ai_decision, execution_status, created_at
        FROM automation_logs
        WHERE user_id=?
        ORDER BY id DESC LIMIT ?
        """,
        (user_id, limit),
    )
    return [dict(r) for r in rows]


def get_workflow_history_summary(
    user_id: int,
    entity_type: str,
    entity_id: int,
    limit: int = 10,
) -> list[dict]:
    rows = execute_query(
        """
        SELECT workflow_name, status, cancelled_reason, created_at
        FROM workflow_instances
        WHERE user_id=? AND entity_type=? AND entity_id=?
        ORDER BY id DESC LIMIT ?
        """,
        (user_id, entity_type, entity_id, limit),
    )
    return [dict(r) for r in rows]
