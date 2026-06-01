"""AI task lifecycle and tool execution persistence."""

import json
import os
from datetime import datetime, timedelta

from automation_log_service import log_ai_task_created, log_tool_executed
from db import (
    TASK_CANCELLED,
    TASK_COMPLETED,
    TASK_DEAD_LETTER,
    TASK_FAILED,
    TASK_PENDING,
    TASK_RETRYING,
    TASK_RUNNING,
    VALID_TASK_TRANSITIONS,
    get_db,
    now_iso,
)

MAX_RETRIES = int(os.environ.get("TASK_MAX_RETRIES", "3"))
RETRY_BASE_SEC = int(os.environ.get("TASK_RETRY_BASE_SEC", "30"))
RETRY_CAP_SEC = int(os.environ.get("TASK_RETRY_CAP_SEC", "1800"))


def _retry_delay_seconds(retry_count: int) -> int:
    """Exponential backoff capped at RETRY_CAP_SEC."""
    delay = RETRY_BASE_SEC * (2 ** max(0, retry_count))
    return min(RETRY_CAP_SEC, delay)


def _retry_at(retry_count: int) -> str:
    return (datetime.utcnow() + timedelta(seconds=_retry_delay_seconds(retry_count))).isoformat()

ACTIVE_TASK_STATUSES = (
    TASK_PENDING,
    TASK_RUNNING,
    TASK_RETRYING,
)


def _can_transition(current: str, new: str) -> bool:
    return new in VALID_TASK_TRANSITIONS.get(current, set())


def task_exists(
    task_type: str,
    entity_type: str,
    entity_id: int,
    workflow_id: int,
) -> bool:
    conn = get_db()
    placeholders = ",".join("?" * len(ACTIVE_TASK_STATUSES))
    row = conn.execute(
        f"""
        SELECT id FROM ai_tasks
        WHERE task_type=?
          AND entity_type=?
          AND entity_id=?
          AND workflow_id=?
          AND status IN ({placeholders})
        """,
        (
            task_type,
            entity_type,
            entity_id,
            workflow_id,
            *ACTIVE_TASK_STATUSES,
        ),
    ).fetchone()
    conn.close()
    exists = row is not None
    print(
        f"[DEBUG] task_exists={exists} type={task_type} "
        f"workflow={workflow_id} entity={entity_type}#{entity_id}"
    )
    return exists


def transition_task(task_id: int, new_status: str, **fields):
    conn = get_db()
    row = conn.execute(
        "SELECT status FROM ai_tasks WHERE id=?", (task_id,)
    ).fetchone()

    if not row:
        conn.close()
        raise ValueError(f"Task #{task_id} not found")

    current = row["status"]
    if not _can_transition(current, new_status):
        conn.close()
        raise ValueError(
            f"Invalid transition: {current} -> {new_status} "
            f"for task #{task_id}"
        )

    updates = ["status=?", "updated_at=?"]
    values = [new_status, now_iso()]

    for key, value in fields.items():
        updates.append(f"{key}=?")
        values.append(value)

    values.append(task_id)
    conn.execute(
        f"UPDATE ai_tasks SET {', '.join(updates)} WHERE id=?",
        values,
    )
    conn.commit()
    conn.close()

    print(f"[TASK] #{task_id} {current} -> {new_status}")


def create_ai_task(
    task_type: str,
    entity_type: str,
    entity_id: int,
    workflow_id: int,
    payload: dict,
    event_id: int = 0,
    user_id: int = 1,
):
    if task_exists(task_type, entity_type, entity_id, workflow_id):
        print(
            f"[AI TASK] Skipped duplicate: {task_type} "
            f"workflow={workflow_id} {entity_type}#{entity_id}"
        )
        return None

    conn = get_db()
    cursor = conn.execute(
        """
        INSERT INTO ai_tasks (
            user_id, task_type, entity_type, entity_id, workflow_id,
            status, payload, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            task_type,
            entity_type,
            entity_id,
            workflow_id,
            TASK_PENDING,
            json.dumps(payload),
            now_iso(),
            now_iso(),
        ),
    )
    task_id = cursor.lastrowid
    conn.commit()
    conn.close()

    log_ai_task_created(
        task_id=task_id,
        task_type=task_type,
        workflow_id=workflow_id,
        entity_type=entity_type,
        entity_id=entity_id,
        event_id=event_id,
        user_id=user_id,
    )

    print(
        f"[AI TASK] Created #{task_id}: {task_type} "
        f"for {entity_type}#{entity_id} (workflow={workflow_id})"
    )
    return task_id


def get_pending_tasks():
    conn = get_db()
    rows = conn.execute(
        """
        SELECT * FROM ai_tasks
        WHERE status IN (?, ?)
          AND (next_retry_at IS NULL OR next_retry_at <= ?)
        ORDER BY id ASC
        """,
        (TASK_PENDING, TASK_RETRYING, now_iso()),
    ).fetchall()
    conn.close()
    return rows


def get_task(task_id: int):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM ai_tasks WHERE id=?", (task_id,)
    ).fetchone()
    conn.close()
    return row


def get_last_tool_execution(task_id: int):
    conn = get_db()
    row = conn.execute(
        """
        SELECT tool_name FROM tool_executions
        WHERE task_id=? AND status='success'
        ORDER BY id DESC LIMIT 1
        """,
        (task_id,),
    ).fetchone()
    conn.close()
    return row["tool_name"] if row else None


def complete_task(task_id: int, result: str, selected_tool: str | None = None):
    fields = {"ai_result": result, "next_retry_at": None}
    if selected_tool:
        fields["selected_tool"] = selected_tool
    transition_task(task_id, TASK_COMPLETED, **fields)

    # Surface success to the learning system via planner_outcomes.
    # Also close out any attached campaign — task completion is the natural
    # "campaign done" boundary in the current orchestration model.
    try:
        row = get_task(task_id)
        if row:
            from planner_memory import record_planner_outcome
            payload = {}
            try:
                payload = json.loads(row["payload"]) if row["payload"] else {}
            except (TypeError, json.JSONDecodeError):
                payload = {}
            record_planner_outcome(
                user_id=row["user_id"] if "user_id" in row.keys() else 1,
                memory_id=None,
                workflow_id=row["workflow_id"] if "workflow_id" in row.keys() else None,
                outcome="completed_success",
                workflow_name=payload.get("workflow_name"),
                business_intent=payload.get("business_intent"),
            )

            # Campaign lifecycle hook — idempotent: complete_campaign no-ops
            # on already-completed/archived rows.
            campaign_id = payload.get("campaign_id")
            if campaign_id:
                try:
                    from campaign_service import complete_campaign
                    complete_campaign(int(campaign_id))
                except Exception as exc:
                    print(f"[TASK] complete_campaign({campaign_id}) skipped: {exc}")
    except Exception as exc:
        print(f"[TASK] outcome reporting skipped: {exc}")


def fail_task(task_id: int, error: str):
    conn = get_db()
    row = conn.execute(
        "SELECT retry_count, status FROM ai_tasks WHERE id=?",
        (task_id,),
    ).fetchone()

    retry_count = row["retry_count"] if row else 0

    if retry_count < MAX_RETRIES:
        next_retry = _retry_at(retry_count)
        conn.execute(
            """
            UPDATE ai_tasks
            SET status=?, retry_count=?, error=?, next_retry_at=?, updated_at=?
            WHERE id=?
            """,
            (
                TASK_RETRYING,
                retry_count + 1,
                error,
                next_retry,
                now_iso(),
                task_id,
            ),
        )
        conn.commit()
        conn.close()
        print(
            f"[TASK] #{task_id} failed, will retry "
            f"({retry_count + 1}/{MAX_RETRIES}) at {next_retry}: {error}"
        )
        return TASK_RETRYING

    conn.execute(
        """
        UPDATE ai_tasks
        SET status=?, error=?, next_retry_at=NULL, updated_at=?
        WHERE id=?
        """,
        (TASK_DEAD_LETTER, error, now_iso(), task_id),
    )
    conn.commit()
    conn.close()
    print(f"[TASK] #{task_id} moved to dead_letter: {error}")
    return TASK_DEAD_LETTER


def cancel_task(task_id: int, reason: str = "cancelled"):
    transition_task(task_id, TASK_CANCELLED, error=reason)


def log_tool_execution(
    task_id: int,
    tool_name: str,
    input_payload: dict,
    output_payload: dict | None,
    status: str,
    duration_ms: int,
    error: str | None = None,
):
    conn = get_db()
    conn.execute(
        """
        INSERT INTO tool_executions (
            task_id, tool_name, input_payload, output_payload,
            status, duration_ms, error, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task_id,
            tool_name,
            json.dumps(input_payload or {}),
            json.dumps(output_payload or {}),
            status,
            duration_ms,
            error,
            now_iso(),
        ),
    )
    conn.commit()
    conn.close()

    task_row = get_task(task_id)
    workflow_id = task_row["workflow_id"] if task_row else None
    task_user_id = task_row["user_id"] if task_row and "user_id" in task_row.keys() else 1

    log_tool_executed(
        task_id=task_id,
        tool_name=tool_name,
        input_payload=input_payload,
        output_payload=output_payload,
        status=status,
        duration_ms=duration_ms,
        workflow_id=workflow_id,
        user_id=task_user_id,
    )

    print(
        f"[TOOL EXEC] task=#{task_id} tool={tool_name} "
        f"status={status} duration={duration_ms}ms"
    )
