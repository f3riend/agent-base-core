"""
Operator-facing scheduling / calendar service.

The runtime already has internal scheduling: `workflow_instances.scheduled_at`
controls when the workflow worker picks up a workflow, and `ai_tasks.next_retry_at`
controls task backoff. Those are runtime-internal. This module is the
operator's calendar: a typed entity an operator schedules from the UI
("Cuma 09:00'da Instagram paylaşımı planla").

Entries land in the `scheduled_entries` table; a polling step (run from the
workflow_worker) fires due entries by creating the matching workflow via
`workflow_service.create_workflow`. The original entry stays as the
historical record (status = fired) so the calendar UI can show what
happened, when.

Design:
    - Entry kinds: "content_post", "campaign", "workflow", "reminder".
    - Recurrence: dateutil.rrule string (if installed) or simple presets.
      We don't import dateutil to stay dependency-light — `_next_occurrence`
      handles "daily", "weekly", "monthly" presets directly.
    - Approval reuse: high-risk entries (external publishing) can route
      through approval_service when fired, exactly like autonomous plans.

NO HTTP, NO LISTENER ECHO. This module emits orchestration_traces for
observability and writes the workflow via the existing service layer.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

from db import DEFAULT_USER_ID, db_connection, execute_query, execute_write, now_iso
from observability import _emit


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


SCHEDULE_PENDING   = "pending"
SCHEDULE_FIRED     = "fired"
SCHEDULE_CANCELLED = "cancelled"
SCHEDULE_FAILED    = "failed"

VALID_KINDS = ("content_post", "campaign", "workflow", "reminder")
VALID_RECURRENCE = ("once", "daily", "weekly", "monthly")


def init_scheduling_tables() -> None:
    """Create the scheduled_entries table + indexes if missing."""
    with db_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scheduled_entries (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL,
                kind            TEXT NOT NULL,
                channel         TEXT,
                title           TEXT,
                description     TEXT,
                scheduled_at    TEXT NOT NULL,
                recurrence      TEXT DEFAULT 'once',
                workflow_name   TEXT,
                payload_json    TEXT,
                requires_approval INTEGER DEFAULT 0,
                status          TEXT DEFAULT 'pending',
                fired_at        TEXT,
                workflow_id     INTEGER,
                last_error      TEXT,
                created_by      TEXT DEFAULT 'operator',
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sched_due "
            "ON scheduled_entries (status, scheduled_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sched_user_window "
            "ON scheduled_entries (user_id, scheduled_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sched_user_status "
            "ON scheduled_entries (user_id, status, scheduled_at)"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso(ts: datetime | str | None) -> str:
    if ts is None:
        return now_iso()
    if isinstance(ts, str):
        return ts
    return ts.isoformat()


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00").replace("+00:00", ""))


def _row_to_entry(row: sqlite3.Row | dict) -> dict:
    d = dict(row)
    raw = d.pop("payload_json", None) or "{}"
    try:
        d["payload"] = json.loads(raw)
    except json.JSONDecodeError:
        d["payload"] = {}
    d["requires_approval"] = bool(d.get("requires_approval"))
    return d


def _validate_kind(kind: str) -> None:
    if kind not in VALID_KINDS:
        raise ValueError(
            f"invalid kind {kind!r}; expected one of {VALID_KINDS}"
        )


def _validate_recurrence(rec: str) -> None:
    if rec not in VALID_RECURRENCE:
        raise ValueError(
            f"invalid recurrence {rec!r}; expected one of {VALID_RECURRENCE}"
        )


def _next_occurrence(after: datetime, recurrence: str) -> datetime | None:
    """Compute the next datetime for a recurring schedule.

    Returns None for 'once'. Presets handled here keep the module
    dependency-light; if we ever need RRULE expressivity we'll add
    python-dateutil.
    """
    if recurrence == "once":
        return None
    if recurrence == "daily":
        return after + timedelta(days=1)
    if recurrence == "weekly":
        return after + timedelta(weeks=1)
    if recurrence == "monthly":
        # Approximate — month length varies; close enough for the calendar.
        month = after.month + 1
        year = after.year + (1 if month > 12 else 0)
        month = month if month <= 12 else month - 12
        try:
            return after.replace(year=year, month=month)
        except ValueError:
            # e.g. Jan 31 → Feb — fall back to last day of target month.
            for day in (30, 29, 28):
                try:
                    return after.replace(year=year, month=month, day=day)
                except ValueError:
                    continue
            return after + timedelta(days=30)
    return None


# ---------------------------------------------------------------------------
# Public scheduling API
# ---------------------------------------------------------------------------


def create_schedule(
    *,
    user_id: int,
    kind: str,
    scheduled_at: datetime | str,
    title: str = "",
    description: str = "",
    channel: str | None = None,
    workflow_name: str | None = None,
    payload: dict | None = None,
    recurrence: str = "once",
    requires_approval: bool = False,
    created_by: str = "operator",
) -> dict:
    """Persist a schedule entry. Returns the stored entry as a dict."""
    _validate_kind(kind)
    _validate_recurrence(recurrence)

    init_scheduling_tables()
    ts = now_iso()
    sa = _iso(scheduled_at)
    body = json.dumps(payload or {}, default=str, ensure_ascii=False)

    new_id = execute_write(
        """
        INSERT INTO scheduled_entries (
            user_id, kind, channel, title, description,
            scheduled_at, recurrence, workflow_name, payload_json,
            requires_approval, status, created_by, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(user_id),
            kind,
            channel,
            title or kind,
            description,
            sa,
            recurrence,
            workflow_name,
            body,
            1 if requires_approval else 0,
            SCHEDULE_PENDING,
            created_by,
            ts,
            ts,
        ),
    )

    entry = get_schedule(new_id)
    _emit(
        "SCHEDULE_CREATED",
        {
            "id": new_id,
            "kind": kind,
            "channel": channel,
            "scheduled_at": sa,
            "recurrence": recurrence,
            "summary": f"{title or kind} planlandı ({sa})",
        },
        persist=True,
        user_id=int(user_id),
    )
    return entry


def schedule_content_post(
    *,
    user_id: int,
    channel: str,
    when: datetime | str,
    payload: dict,
    title: str = "",
    description: str = "",
    recurrence: str = "once",
    requires_approval: bool | None = None,
) -> dict:
    """Schedule a content post on a given channel.

    `payload` is fed verbatim to the firing workflow (e.g. headline, hook,
    target_audience, hashtags). The channel determines the eventual tool:
    instagram → instagram_campaign_tool, banner → banner_generator_tool.
    """
    channel_to_tool = {
        "instagram":   "instagram_campaign_tool",
        "banner":      "banner_generator_tool",
        "coupon":      "coupon_generator_tool",
        "faq":         "faq_update_tool",
        "support":     "support_response_tool",
        "trend":       "trend_analysis_tool",
    }
    tool = channel_to_tool.get(channel)
    if not tool:
        raise ValueError(
            f"unsupported channel {channel!r}; "
            f"expected one of {list(channel_to_tool)}"
        )

    # Default: external publishing → require approval.
    if requires_approval is None:
        requires_approval = channel == "instagram"

    workflow_name = f"scheduled_{channel}_post"
    enriched_payload = dict(payload)
    enriched_payload.setdefault("tools", [tool])
    enriched_payload.setdefault("channel", channel)

    return create_schedule(
        user_id=user_id,
        kind="content_post",
        scheduled_at=when,
        title=title or f"{channel.title()} paylaşımı",
        description=description,
        channel=channel,
        workflow_name=workflow_name,
        payload=enriched_payload,
        recurrence=recurrence,
        requires_approval=requires_approval,
    )


def schedule_campaign(
    *,
    user_id: int,
    when: datetime | str,
    title: str,
    description: str = "",
    intent: str = "marketing_campaign",
    channel: str = "instagram",
    payload: dict | None = None,
    requires_approval: bool = True,
) -> dict:
    """Schedule a campaign — typically routes through approval."""
    return create_schedule(
        user_id=user_id,
        kind="campaign",
        scheduled_at=when,
        title=title,
        description=description,
        channel=channel,
        workflow_name=f"scheduled_campaign_{intent}",
        payload={
            "business_intent": intent,
            "channel": channel,
            **(payload or {}),
        },
        requires_approval=requires_approval,
    )


def schedule_workflow(
    *,
    user_id: int,
    when: datetime | str,
    workflow_name: str,
    entity_type: str = "store",
    entity_id: int | None = None,
    title: str = "",
    description: str = "",
    payload: dict | None = None,
    recurrence: str = "once",
    requires_approval: bool = False,
) -> dict:
    """Schedule a generic workflow by name."""
    enriched = {
        "entity_type": entity_type,
        "entity_id": entity_id,
        **(payload or {}),
    }
    return create_schedule(
        user_id=user_id,
        kind="workflow",
        scheduled_at=when,
        title=title or workflow_name,
        description=description,
        workflow_name=workflow_name,
        payload=enriched,
        recurrence=recurrence,
        requires_approval=requires_approval,
    )


def schedule_recurring(
    *,
    user_id: int,
    kind: str,
    starts_at: datetime | str,
    recurrence: str,
    title: str,
    description: str = "",
    channel: str | None = None,
    workflow_name: str | None = None,
    payload: dict | None = None,
) -> dict:
    """Recurring schedule shortcut — non-'once' only."""
    if recurrence == "once":
        raise ValueError("schedule_recurring requires recurrence != 'once'")
    return create_schedule(
        user_id=user_id,
        kind=kind,
        scheduled_at=starts_at,
        title=title,
        description=description,
        channel=channel,
        workflow_name=workflow_name,
        payload=payload,
        recurrence=recurrence,
    )


def get_schedule(schedule_id: int) -> dict | None:
    row = execute_query(
        "SELECT * FROM scheduled_entries WHERE id=?",
        (int(schedule_id),),
        one=True,
    )
    return _row_to_entry(row) if row else None


def list_schedules(
    *,
    user_id: int,
    status: str | None = None,
    kind: str | None = None,
    limit: int = 100,
) -> list[dict]:
    init_scheduling_tables()
    sql = "SELECT * FROM scheduled_entries WHERE user_id=?"
    params: list[Any] = [int(user_id)]
    if status:
        sql += " AND status=?"
        params.append(status)
    if kind:
        sql += " AND kind=?"
        params.append(kind)
    sql += " ORDER BY scheduled_at ASC LIMIT ?"
    params.append(int(limit))
    rows = execute_query(sql, tuple(params))
    return [_row_to_entry(r) for r in rows]


def list_calendar_window(
    *,
    user_id: int,
    start: datetime | str,
    end: datetime | str,
    statuses: tuple[str, ...] = (SCHEDULE_PENDING, SCHEDULE_FIRED),
) -> dict:
    """Return entries within [start, end] grouped by day for calendar UI."""
    init_scheduling_tables()
    start_iso = _iso(start)
    end_iso = _iso(end)
    placeholders = ",".join("?" * len(statuses))
    rows = execute_query(
        f"""
        SELECT * FROM scheduled_entries
        WHERE user_id=?
          AND scheduled_at >= ?
          AND scheduled_at <= ?
          AND status IN ({placeholders})
        ORDER BY scheduled_at ASC
        """,
        (int(user_id), start_iso, end_iso, *statuses),
    )

    by_day: dict[str, list[dict]] = {}
    for row in rows:
        entry = _row_to_entry(row)
        try:
            day_key = _parse_iso(entry["scheduled_at"]).date().isoformat()
        except (ValueError, TypeError):
            day_key = "unknown"
        by_day.setdefault(day_key, []).append(entry)

    return {
        "start": start_iso,
        "end": end_iso,
        "days": [
            {"date": day, "entries": entries, "count": len(entries)}
            for day, entries in sorted(by_day.items())
        ],
        "total": sum(len(v) for v in by_day.values()),
    }


def move_schedule(
    schedule_id: int,
    new_when: datetime | str,
    *,
    reason: str = "operator_reschedule",
) -> dict:
    entry = get_schedule(schedule_id)
    if not entry:
        raise ValueError(f"schedule not found: {schedule_id}")
    if entry["status"] != SCHEDULE_PENDING:
        raise ValueError(
            f"cannot move schedule in status={entry['status']!r}; "
            "only pending entries can be rescheduled"
        )
    new_iso = _iso(new_when)
    execute_write(
        """
        UPDATE scheduled_entries
        SET scheduled_at=?, updated_at=?
        WHERE id=?
        """,
        (new_iso, now_iso(), int(schedule_id)),
    )
    _emit(
        "SCHEDULE_MOVED",
        {
            "id": schedule_id,
            "from": entry["scheduled_at"],
            "to": new_iso,
            "reason": reason,
            "summary": f"{entry.get('title','plan')} ertelendi → {new_iso}",
        },
        persist=True,
        user_id=entry["user_id"],
    )
    return get_schedule(schedule_id) or entry


def cancel_schedule(
    schedule_id: int,
    *,
    reason: str = "operator_cancel",
) -> dict:
    entry = get_schedule(schedule_id)
    if not entry:
        raise ValueError(f"schedule not found: {schedule_id}")
    if entry["status"] in (SCHEDULE_CANCELLED, SCHEDULE_FIRED, SCHEDULE_FAILED):
        return entry  # idempotent
    execute_write(
        """
        UPDATE scheduled_entries
        SET status=?, last_error=?, updated_at=?
        WHERE id=?
        """,
        (SCHEDULE_CANCELLED, reason, now_iso(), int(schedule_id)),
    )
    _emit(
        "SCHEDULE_CANCELLED",
        {"id": schedule_id, "reason": reason},
        persist=True,
        user_id=entry["user_id"],
    )
    return get_schedule(schedule_id) or entry


# ---------------------------------------------------------------------------
# Firing — called from the workflow_worker poll loop
# ---------------------------------------------------------------------------


def due_schedules(now: datetime | None = None, *, limit: int = 50) -> list[dict]:
    """Return pending schedules whose time has come."""
    init_scheduling_tables()
    cutoff = _iso(now or datetime.utcnow())
    rows = execute_query(
        """
        SELECT * FROM scheduled_entries
        WHERE status=? AND scheduled_at <= ?
        ORDER BY scheduled_at ASC LIMIT ?
        """,
        (SCHEDULE_PENDING, cutoff, int(limit)),
    )
    return [_row_to_entry(r) for r in rows]


def _mark_fired(schedule_id: int, workflow_id: int | None) -> None:
    execute_write(
        """
        UPDATE scheduled_entries
        SET status=?, fired_at=?, workflow_id=?, updated_at=?
        WHERE id=?
        """,
        (SCHEDULE_FIRED, now_iso(), workflow_id, now_iso(), int(schedule_id)),
    )


def _mark_failed(schedule_id: int, error: str) -> None:
    execute_write(
        """
        UPDATE scheduled_entries
        SET status=?, last_error=?, updated_at=?
        WHERE id=?
        """,
        (SCHEDULE_FAILED, error[:400], now_iso(), int(schedule_id)),
    )


def _maybe_create_followup(entry: dict) -> None:
    """If recurring, create the next occurrence as a fresh pending entry."""
    recurrence = entry.get("recurrence", "once")
    if recurrence == "once":
        return
    try:
        current = _parse_iso(entry["scheduled_at"])
    except (ValueError, TypeError):
        return
    nxt = _next_occurrence(current, recurrence)
    if not nxt:
        return
    create_schedule(
        user_id=entry["user_id"],
        kind=entry["kind"],
        scheduled_at=nxt,
        title=entry.get("title", ""),
        description=entry.get("description", ""),
        channel=entry.get("channel"),
        workflow_name=entry.get("workflow_name"),
        payload=entry.get("payload"),
        recurrence=recurrence,
        requires_approval=bool(entry.get("requires_approval")),
        created_by=entry.get("created_by", "recurring"),
    )


def fire_schedule(entry: dict) -> dict:
    """Convert a due schedule entry into a workflow_instance."""
    from workflow_service import create_workflow

    schedule_id = entry["id"]
    user_id = entry["user_id"]
    workflow_name = entry.get("workflow_name") or f"scheduled_{entry['kind']}"
    payload = entry.get("payload") or {}
    entity_type = (payload.get("entity_type") or "store").lower()
    entity_id = payload.get("entity_id") or 0

    metadata = {
        "source": "scheduled",
        "schedule_id": schedule_id,
        "schedule_kind": entry["kind"],
        "schedule_channel": entry.get("channel"),
        "task_payload": payload,
        "tools": payload.get("tools", []),
        "business_intent": payload.get("business_intent", "scheduled"),
        "requires_approval": bool(entry.get("requires_approval")),
        "approved": not bool(entry.get("requires_approval")),
        "title": entry.get("title"),
    }

    try:
        workflow_id = create_workflow(
            workflow_name=workflow_name,
            entity_type=entity_type,
            entity_id=int(entity_id) if entity_id else 0,
            delay_days=0,
            event_id=0,
            user_id=user_id,
            metadata=metadata,
        )
    except Exception as exc:
        _mark_failed(schedule_id, str(exc))
        _emit(
            "SCHEDULE_FAILED",
            {"id": schedule_id, "error": str(exc)},
            persist=True,
            user_id=user_id,
        )
        raise

    _mark_fired(schedule_id, workflow_id)
    _emit(
        "SCHEDULE_FIRED",
        {
            "id": schedule_id,
            "workflow_id": workflow_id,
            "workflow_name": workflow_name,
            "summary": f"{entry.get('title','plan')} → iş akışı #{workflow_id}",
        },
        persist=True,
        user_id=user_id,
        workflow_id=workflow_id,
    )

    # Recurring → schedule the next instance
    _maybe_create_followup(entry)

    return get_schedule(schedule_id) or entry


def fire_due_schedules(*, now: datetime | None = None, limit: int = 50) -> dict:
    """Operator-callable / worker-callable: fire everything that's due now."""
    fired: list[dict] = []
    errors: list[dict] = []
    for entry in due_schedules(now=now, limit=limit):
        try:
            fired.append(fire_schedule(entry))
        except Exception as exc:
            errors.append({"id": entry["id"], "error": str(exc)})
    return {"fired": fired, "errors": errors, "count": len(fired)}


# Initialise table on module import — keeps the API endpoints simple.
init_scheduling_tables()
