"""Orchestration automation logs (listener DB).

Every public function takes user_id explicitly. The DB default (1) is no
longer used as a silent fallback for callers that omit the tenant binding —
that was the source of cross-tenant log contamination.
"""

import json

from db import DEFAULT_USER_ID, get_db, now_iso


def _insert_log(
    *,
    user_id: int,
    rule_name: str,
    ai_decision: str,
    event_id: int = 0,
    workflow_id: int | None = None,
    task_id: int | None = None,
    selected_tool: str | None = None,
    tool_input: dict | None = None,
):
    conn = get_db()
    cursor = conn.execute(
        """
        INSERT INTO automation_logs (
            user_id, event_id, rule_name, workflow_id, task_id,
            ai_decision, selected_tool, tool_input,
            execution_status, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(user_id),
            event_id,
            rule_name,
            workflow_id,
            task_id,
            ai_decision,
            selected_tool,
            json.dumps(tool_input or {}),
            "pending",
            now_iso(),
            now_iso(),
        ),
    )
    log_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return log_id


def _finish_log(
    log_id: int,
    status: str,
    tool_output: dict | None = None,
    failed_reason: str | None = None,
    latency_ms: int = 0,
    selected_tool: str | None = None,
):
    conn = get_db()
    if selected_tool:
        conn.execute(
            """
            UPDATE automation_logs
            SET execution_status=?, tool_output=?, failed_reason=?,
                latency_ms=?, selected_tool=?, updated_at=?
            WHERE id=?
            """,
            (
                status,
                json.dumps(tool_output or {}),
                failed_reason,
                latency_ms,
                selected_tool,
                now_iso(),
                log_id,
            ),
        )
    else:
        conn.execute(
            """
            UPDATE automation_logs
            SET execution_status=?, tool_output=?, failed_reason=?,
                latency_ms=?, updated_at=?
            WHERE id=?
            """,
            (
                status,
                json.dumps(tool_output or {}),
                failed_reason,
                latency_ms,
                now_iso(),
                log_id,
            ),
        )
    conn.commit()
    conn.close()


def log_workflow_created(
    workflow_id: int,
    workflow_name: str,
    entity_type: str,
    entity_id: int,
    *,
    user_id: int = DEFAULT_USER_ID,
    event_id: int = 0,
):
    log_id = _insert_log(
        user_id=user_id,
        event_id=event_id,
        rule_name=f"workflow:{workflow_name}",
        workflow_id=workflow_id,
        ai_decision=f"workflow_created for {entity_type}#{entity_id}",
        tool_input={
            "workflow_id": workflow_id,
            "workflow_name": workflow_name,
            "entity_type": entity_type,
            "entity_id": entity_id,
        },
    )
    _finish_log(
        log_id,
        status="success",
        tool_output={"workflow_id": workflow_id},
    )
    print(f"[AUTOMATION LOG] workflow_created #{workflow_id} user={user_id}")


def log_workflow_cancelled(
    workflow_id: int | None,
    workflow_name: str,
    entity_type: str,
    entity_id: int,
    reason: str,
    *,
    user_id: int = DEFAULT_USER_ID,
    event_id: int = 0,
):
    log_id = _insert_log(
        user_id=user_id,
        event_id=event_id,
        rule_name=f"workflow:{workflow_name}",
        workflow_id=workflow_id,
        ai_decision=f"workflow_cancelled: {reason}",
        tool_input={
            "entity_type": entity_type,
            "entity_id": entity_id,
            "reason": reason,
        },
    )
    _finish_log(
        log_id,
        status="cancelled",
        tool_output={"reason": reason},
    )
    print(f"[AUTOMATION LOG] workflow_cancelled {workflow_name} user={user_id}")


def log_ai_task_created(
    task_id: int,
    task_type: str,
    workflow_id: int,
    entity_type: str,
    entity_id: int,
    *,
    user_id: int = DEFAULT_USER_ID,
    event_id: int = 0,
):
    log_id = _insert_log(
        user_id=user_id,
        event_id=event_id,
        rule_name=f"task:{task_type}",
        workflow_id=workflow_id,
        task_id=task_id,
        ai_decision=f"ai_task_created for {entity_type}#{entity_id}",
        tool_input={
            "task_id": task_id,
            "task_type": task_type,
            "workflow_id": workflow_id,
        },
    )
    _finish_log(
        log_id,
        status="success",
        tool_output={"task_id": task_id},
    )
    print(f"[AUTOMATION LOG] ai_task_created #{task_id} user={user_id}")


def log_tool_executed(
    task_id: int,
    tool_name: str,
    input_payload: dict,
    output_payload: dict | None,
    status: str,
    duration_ms: int,
    *,
    user_id: int = DEFAULT_USER_ID,
    workflow_id: int | None = None,
    event_id: int = 0,
):
    log_id = _insert_log(
        user_id=user_id,
        event_id=event_id,
        rule_name=f"tool:{tool_name}",
        workflow_id=workflow_id,
        task_id=task_id,
        ai_decision="tool_executed",
        selected_tool=tool_name,
        tool_input=input_payload,
    )
    _finish_log(
        log_id,
        status=status,
        tool_output=output_payload,
        latency_ms=duration_ms,
        selected_tool=tool_name,
    )
