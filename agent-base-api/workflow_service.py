"""Workflow orchestration — no listener imports."""

import json
import sqlite3
import time
from datetime import datetime, timedelta

from automation_log_service import (
    log_workflow_cancelled,
    log_workflow_created,
)
from db import (
    WORKFLOW_CANCELLED,
    WORKFLOW_COMPLETED,
    WORKFLOW_RUNNING,
    WORKFLOW_SCHEDULED,
    get_db,
    now_iso,
)
from tool_registry import CRITICAL_TASK_MAP

IDEMPOTENT_WORKFLOW_STATUSES = (
    WORKFLOW_SCHEDULED,
    WORKFLOW_RUNNING,
    WORKFLOW_COMPLETED,
)

CRITICAL_WORKFLOW_NAMES = frozenset(CRITICAL_TASK_MAP.keys())


def workflow_exists(workflow_name, entity_type, entity_id):
    conn = get_db()
    placeholders = ",".join("?" * len(IDEMPOTENT_WORKFLOW_STATUSES))
    row = conn.execute(
        f"""
        SELECT id FROM workflow_instances
        WHERE workflow_name=?
          AND entity_type=?
          AND entity_id=?
          AND status IN ({placeholders})
        """,
        (
            workflow_name,
            entity_type,
            entity_id,
            *IDEMPOTENT_WORKFLOW_STATUSES,
        ),
    ).fetchone()
    conn.close()
    exists = row is not None
    print(
        f"[DEBUG] workflow_exists={exists} "
        f"name={workflow_name} entity={entity_type}#{entity_id}"
    )
    return exists


def create_workflow(
    workflow_name: str,
    entity_type: str,
    entity_id: int,
    delay_days: int = 0,
    event_id: int = 0,
    user_id: int = 1,
    metadata: dict | None = None,
):
    if workflow_exists(workflow_name, entity_type, entity_id):
        print(
            f"[WORKFLOW] Skipped duplicate: {workflow_name} "
            f"for {entity_type}#{entity_id}"
        )
        return None

    scheduled_at = (
        datetime.utcnow() + timedelta(days=delay_days)
    ).isoformat()

    meta_json = json.dumps(metadata or {})

    conn = get_db()
    try:
        cursor = conn.execute(
            """
            INSERT INTO workflow_instances (
                user_id, workflow_name, entity_type, entity_id,
                status, scheduled_at, metadata, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                workflow_name,
                entity_type,
                entity_id,
                WORKFLOW_SCHEDULED,
                scheduled_at,
                meta_json,
                now_iso(),
                now_iso(),
            ),
        )
        workflow_id = cursor.lastrowid
        conn.commit()
    except sqlite3.IntegrityError:
        # Partial unique index idx_workflow_active_unique caught a race.
        existing = conn.execute(
            """
            SELECT id FROM workflow_instances
            WHERE user_id=? AND workflow_name=? AND entity_type=? AND entity_id=?
              AND status IN (?, ?)
            ORDER BY id DESC LIMIT 1
            """,
            (
                user_id,
                workflow_name,
                entity_type,
                entity_id,
                WORKFLOW_SCHEDULED,
                WORKFLOW_RUNNING,
            ),
        ).fetchone()
        conn.close()
        if existing:
            print(
                f"[WORKFLOW] Race deduped: {workflow_name} for "
                f"{entity_type}#{entity_id} → existing #{existing['id']}"
            )
            return None
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass

    log_workflow_created(
        workflow_id=workflow_id,
        workflow_name=workflow_name,
        entity_type=entity_type,
        entity_id=entity_id,
        event_id=event_id,
        user_id=user_id,
    )

    print(
        f"[WORKFLOW] Created #{workflow_id}: {workflow_name} "
        f"for {entity_type}#{entity_id} "
        f"(delay={delay_days}d, autonomous={workflow_name not in CRITICAL_WORKFLOW_NAMES})"
    )
    return workflow_id


def cancel_workflows(
    entity_type: str,
    entity_id: int,
    reason: str,
    workflow_name: str | None = None,
    event_id: int = 0,
    user_id: int = 1,
):
    conn = get_db()

    if workflow_name:
        rows = conn.execute(
            """
            SELECT id FROM workflow_instances
            WHERE entity_type=? AND entity_id=?
              AND workflow_name=?
              AND status IN (?, ?)
            """,
            (
                entity_type,
                entity_id,
                workflow_name,
                WORKFLOW_SCHEDULED,
                WORKFLOW_RUNNING,
            ),
        ).fetchall()
        conn.execute(
            """
            UPDATE workflow_instances
            SET status=?, cancelled_reason=?, updated_at=?
            WHERE entity_type=? AND entity_id=?
              AND workflow_name=?
              AND status IN (?, ?)
            """,
            (
                WORKFLOW_CANCELLED,
                reason,
                now_iso(),
                entity_type,
                entity_id,
                workflow_name,
                WORKFLOW_SCHEDULED,
                WORKFLOW_RUNNING,
            ),
        )
    else:
        rows = conn.execute(
            """
            SELECT id FROM workflow_instances
            WHERE entity_type=? AND entity_id=?
              AND status IN (?, ?)
            """,
            (
                entity_type,
                entity_id,
                WORKFLOW_SCHEDULED,
                WORKFLOW_RUNNING,
            ),
        ).fetchall()
        conn.execute(
            """
            UPDATE workflow_instances
            SET status=?, cancelled_reason=?, updated_at=?
            WHERE entity_type=? AND entity_id=?
              AND status IN (?, ?)
            """,
            (
                WORKFLOW_CANCELLED,
                reason,
                now_iso(),
                entity_type,
                entity_id,
                WORKFLOW_SCHEDULED,
                WORKFLOW_RUNNING,
            ),
        )

    conn.commit()
    conn.close()

    label = workflow_name or "all"
    for row in rows:
        log_workflow_cancelled(
            workflow_id=row["id"],
            workflow_name=workflow_name or label,
            entity_type=entity_type,
            entity_id=entity_id,
            reason=reason,
            event_id=event_id,
            user_id=user_id,
        )

    print(
        f"[WORKFLOW] Cancelled {label} for "
        f"{entity_type}#{entity_id} (reason={reason})"
    )


def get_pending_workflows():
    conn = get_db()
    rows = conn.execute(
        """
        SELECT * FROM workflow_instances
        WHERE status=?
        ORDER BY scheduled_at ASC
        """,
        (WORKFLOW_SCHEDULED,),
    ).fetchall()
    conn.close()
    return rows


def update_workflow_status(workflow_id: int, status: str, reason: str | None = None):
    conn = get_db()
    if reason:
        conn.execute(
            """
            UPDATE workflow_instances
            SET status=?, cancelled_reason=?, updated_at=?
            WHERE id=?
            """,
            (status, reason, now_iso(), workflow_id),
        )
    else:
        conn.execute(
            """
            UPDATE workflow_instances
            SET status=?, updated_at=?
            WHERE id=?
            """,
            (status, now_iso(), workflow_id),
        )
    conn.commit()
    conn.close()
    print(f"[WORKFLOW] #{workflow_id} status -> {status}")


def should_run(workflow):
    scheduled_at = datetime.fromisoformat(workflow["scheduled_at"])
    return datetime.utcnow() >= scheduled_at


def _parse_metadata(workflow) -> dict:
    raw = workflow["metadata"] if "metadata" in workflow.keys() else None
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def validate_workflow(workflow):
    """Return (valid, cancel_reason). Critical workflows have strict checks."""
    from resource_service import get_item, get_store

    workflow_name = workflow["workflow_name"]
    entity_type = workflow["entity_type"]
    entity_id = workflow["entity_id"]

    if workflow_name in CRITICAL_WORKFLOW_NAMES:
        if workflow_name == "welcome_instagram_post":
            if entity_type != "store":
                return False, "invalid_entity_type"
            store = get_store(entity_id)
            if not store:
                return False, "store_not_found"
            status = store["status"] if "status" in store.keys() else "active"
            if status in ("rejected", "cancelled", "deleted"):
                return False, f"store_{status}"
            return True, None

        if workflow_name == "low_stock_alert":
            if entity_type != "item":
                return False, "invalid_entity_type"
            item = get_item(entity_id)
            if not item:
                return False, "item_not_found"
            item_status = item["status"] if "status" in item.keys() else "active"
            if (item_status or "active") == "deleted":
                return False, "item_deleted"
            if item["stock"] >= 10:
                return False, "stock_recovered"
            return True, None

    metadata = _parse_metadata(workflow)
    if metadata.get("requires_approval") and not metadata.get("approved"):
        return False, "awaiting_approval"

    return True, None


def _execute_critical_workflow(workflow, workflow_id, user_id):
    from resource_service import get_item, get_store
    from task_service import create_ai_task

    workflow_name = workflow["workflow_name"]
    mapping = CRITICAL_TASK_MAP.get(workflow_name)

    if workflow_name == "welcome_instagram_post":
        store = get_store(workflow["entity_id"])
        return create_ai_task(
            task_type=mapping["task_type"],
            entity_type="store",
            entity_id=workflow["entity_id"],
            workflow_id=workflow_id,
            user_id=user_id,
            payload={
                "store": dict(store),
                "goal": "Create launch campaign",
                "platform": "instagram",
            },
        )

    if workflow_name == "low_stock_alert":
        item = get_item(workflow["entity_id"])
        return create_ai_task(
            task_type=mapping["task_type"],
            entity_type="item",
            entity_id=workflow["entity_id"],
            workflow_id=workflow_id,
            user_id=user_id,
            payload={
                "item": dict(item),
                "goal": "Analyze low stock",
            },
        )
    return None


def _execute_autonomous_workflow(workflow, workflow_id, user_id):
    """Execute AI-planned workflows using metadata from autonomous planner."""
    from resource_service import get_item, get_store
    from task_service import create_ai_task

    metadata = _parse_metadata(workflow)
    plan = metadata.get("plan") or metadata

    task_type = (
        plan.get("task_type")
        or metadata.get("task_type")
        or f"autonomous_{workflow['workflow_name'][:40]}"
    )

    payload = (
        plan.get("task_payload")
        or metadata.get("task_payload")
        or {"goal": plan.get("reason", metadata.get("reason", "Autonomous campaign"))}
    )

    if "tools" not in payload:
        payload["tools"] = plan.get("tools") or metadata.get("tools", [])

    entity_type = workflow["entity_type"]
    entity_id = workflow["entity_id"]

    if entity_type == "item" and "item" not in payload:
        item = get_item(entity_id)
        if item:
            payload["item"] = dict(item)
    if entity_type == "store" and "store" not in payload:
        store = get_store(entity_id)
        if store:
            payload["store"] = dict(store)

    payload["workflow_name"] = workflow["workflow_name"]
    payload["autonomous"] = True

    # Campaign lifecycle hook — if the plan carried a campaign_id, this is
    # the moment it transitions from draft/scheduled → live. Propagate the
    # id into the task payload so task_service.complete_task can flip it
    # to `completed` when the AI task finishes. Best-effort: any failure
    # is logged and the workflow continues.
    campaign_id = (
        plan.get("campaign_id")
        or metadata.get("campaign_id")
        or payload.get("campaign_id")
    )
    if campaign_id:
        payload["campaign_id"] = int(campaign_id)
        try:
            from campaign_service import launch_campaign
            launch_campaign(int(campaign_id))
        except Exception as exc:
            print(
                f"[WORKFLOW] launch_campaign({campaign_id}) failed: {exc} "
                f"— workflow continues"
            )

    return create_ai_task(
        task_type=task_type,
        entity_type=entity_type,
        entity_id=entity_id,
        workflow_id=workflow_id,
        user_id=user_id,
        payload=payload,
    )


def execute_workflow(workflow):
    started = time.monotonic()
    workflow_id = workflow["id"]
    workflow_name = workflow["workflow_name"]
    user_id = workflow["user_id"] if "user_id" in workflow.keys() else 1

    print(f"\n[WORKFLOW] Executing {workflow_name} (#{workflow_id})")

    valid, reason = validate_workflow(workflow)

    if not valid:
        update_workflow_status(workflow_id, WORKFLOW_CANCELLED, reason=reason)
        log_workflow_cancelled(
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            entity_type=workflow["entity_type"],
            entity_id=workflow["entity_id"],
            reason=reason,
            user_id=user_id,
        )
        print(f"[WORKFLOW] Cancelled (#{workflow_id}, reason={reason})")
        return

    update_workflow_status(workflow_id, WORKFLOW_RUNNING)

    try:
        if workflow_name in CRITICAL_WORKFLOW_NAMES:
            task_id = _execute_critical_workflow(workflow, workflow_id, user_id)
        else:
            task_id = _execute_autonomous_workflow(workflow, workflow_id, user_id)

        if task_id is None:
            print(f"[WORKFLOW] No new task for #{workflow_id} (duplicate/skipped)")

        update_workflow_status(workflow_id, WORKFLOW_COMPLETED)
        elapsed = int((time.monotonic() - started) * 1000)
        print(f"[WORKFLOW] Completed (#{workflow_id})")
        print(f"[METRIC] workflow_execution_ms={elapsed}")

    except Exception as exc:
        update_workflow_status(workflow_id, WORKFLOW_CANCELLED, reason=str(exc))
        log_workflow_cancelled(
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            entity_type=workflow["entity_type"],
            entity_id=workflow["entity_id"],
            reason=str(exc),
            user_id=user_id,
        )
        print(f"[WORKFLOW] Failed (#{workflow_id}): {exc}")
        raise
