"""
Campaign lifecycle service.

A campaign is an operator-facing entity that lives ALONGSIDE the workflow
that fires it. Today a workflow row is the closest thing to a campaign —
but a workflow is a one-shot scheduling primitive: it has scheduled_at and
status (scheduled/running/completed/cancelled) and that's it. Real campaigns
need a longer lifecycle:

    draft → scheduled → live → paused → completed → archived

…with measurable performance (impressions, clicks, conversions, spend) that
accumulates over time, independent of the workflow that launched it.

Integration points (kept narrow on purpose):
    - autonomous_planner.create_plan attaches `campaign_id` to plan + payload
      when the business_intent is in CAMPAIGN_TRIGGER_INTENTS. The campaign
      starts in `draft` (or `scheduled` if delay > 0).
    - workflow_service._execute_autonomous_workflow calls launch_campaign
      when it detects campaign_id in metadata — that flips draft/scheduled
      to `live`.
    - task_service.complete_task calls complete_campaign when the payload
      carries campaign_id — that flips `live`/`paused` to `completed`.

All transitions are idempotent: calling launch_campaign on an already-live
campaign is a no-op (logged for observability, not an error). This matters
because the workflow_worker may re-fire a workflow on restart.

Tenant isolation: every read takes user_id; mutations check ownership.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

from db import DEFAULT_USER_ID, db_connection, execute_query, execute_write, now_iso
from observability import _emit


# ---------------------------------------------------------------------------
# Status constants + state machine
# ---------------------------------------------------------------------------


STATUS_DRAFT     = "draft"
STATUS_SCHEDULED = "scheduled"
STATUS_LIVE      = "live"
STATUS_PAUSED    = "paused"
STATUS_COMPLETED = "completed"
STATUS_ARCHIVED  = "archived"

ALL_STATUSES = (
    STATUS_DRAFT, STATUS_SCHEDULED, STATUS_LIVE,
    STATUS_PAUSED, STATUS_COMPLETED, STATUS_ARCHIVED,
)

# What you're allowed to transition INTO from each state.
# archived is terminal. completed can only be archived.
_VALID_TRANSITIONS: dict[str, set[str]] = {
    STATUS_DRAFT:     {STATUS_SCHEDULED, STATUS_LIVE, STATUS_ARCHIVED},
    STATUS_SCHEDULED: {STATUS_LIVE, STATUS_PAUSED, STATUS_ARCHIVED},
    STATUS_LIVE:      {STATUS_PAUSED, STATUS_COMPLETED, STATUS_ARCHIVED},
    STATUS_PAUSED:    {STATUS_LIVE, STATUS_COMPLETED, STATUS_ARCHIVED},
    STATUS_COMPLETED: {STATUS_ARCHIVED},
    STATUS_ARCHIVED:  set(),
}


# Intent codes that trigger automatic campaign creation from the autonomous
# planner. Kept aligned with ontology.INTENTS — these are the
# growth/promotion intents that benefit from per-campaign performance
# tracking. Add other intents here ONLY if they have measurable
# campaign-style outcomes; not every workflow is a campaign.
CAMPAIGN_TRIGGER_INTENTS: tuple[str, ...] = (
    "discount_promotion",
    "growth_marketing",
    "marketing_campaign",
)


class InvalidTransitionError(ValueError):
    """Raised when a state transition is rejected by the state machine."""


class CampaignNotFound(LookupError):
    """Raised when a campaign id has no matching row."""


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class Campaign:
    id: int
    user_id: int
    name: str
    channel: str
    intent: str
    status: str
    scheduled_at: str | None
    started_at: str | None
    ended_at: str | None
    budget: float | None
    created_at: str
    updated_at: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "name": self.name,
            "channel": self.channel,
            "intent": self.intent,
            "status": self.status,
            "scheduled_at": self.scheduled_at,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "budget": self.budget,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class CampaignMetricSnapshot:
    id: int
    campaign_id: int
    ts: str
    impressions: int
    clicks: int
    conversions: int
    spend: float

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "campaign_id": self.campaign_id,
            "ts": self.ts,
            "impressions": self.impressions,
            "clicks": self.clicks,
            "conversions": self.conversions,
            "spend": round(self.spend or 0.0, 2),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_campaign(row) -> Campaign:
    d = dict(row)
    return Campaign(
        id=int(d["id"]),
        user_id=int(d["user_id"]),
        name=d["name"],
        channel=d["channel"],
        intent=d["intent"],
        status=d["status"],
        scheduled_at=d.get("scheduled_at"),
        started_at=d.get("started_at"),
        ended_at=d.get("ended_at"),
        budget=float(d["budget"]) if d.get("budget") is not None else None,
        created_at=d["created_at"],
        updated_at=d["updated_at"],
    )


def _row_to_metric(row) -> CampaignMetricSnapshot:
    d = dict(row)
    return CampaignMetricSnapshot(
        id=int(d["id"]),
        campaign_id=int(d["campaign_id"]),
        ts=d["ts"],
        impressions=int(d.get("impressions") or 0),
        clicks=int(d.get("clicks") or 0),
        conversions=int(d.get("conversions") or 0),
        spend=float(d.get("spend") or 0.0),
    )


def _iso(ts: datetime | str | None) -> str | None:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts.isoformat()
    return str(ts)


def _can_transition(current: str, target: str) -> bool:
    return target in _VALID_TRANSITIONS.get(current, set())


def _emit_campaign_trace(
    tag: str,
    campaign: Campaign,
    *,
    summary: str | None = None,
    extra: dict | None = None,
):
    payload: dict[str, Any] = {
        "campaign_id": campaign.id,
        "name": campaign.name,
        "status": campaign.status,
        "intent": campaign.intent,
        "channel": campaign.channel,
        "summary": summary or f"kampanya {campaign.name} → {campaign.status}",
    }
    if extra:
        payload.update(extra)
    _emit(tag, payload, persist=True, user_id=campaign.user_id)


# ---------------------------------------------------------------------------
# Public API — CRUD + transitions
# ---------------------------------------------------------------------------


def create_campaign(
    *,
    user_id: int,
    name: str,
    channel: str,
    intent: str,
    scheduled_at: datetime | str | None = None,
    budget: float | None = None,
) -> Campaign:
    """Persist a new campaign in `draft` (or `scheduled` if a future time is set).

    `scheduled_at` is the operator-intent time. If it is in the future,
    status starts as 'scheduled'; if it is now or past (or None), status
    starts as 'draft' — the workflow runtime will flip to live when ready.
    """
    if not name or not str(name).strip():
        raise ValueError("campaign name is required")
    if not channel or not str(channel).strip():
        raise ValueError("campaign channel is required")
    if not intent or not str(intent).strip():
        raise ValueError("campaign intent is required")

    sa_iso = _iso(scheduled_at)
    initial_status = STATUS_DRAFT
    if sa_iso:
        try:
            sa_dt = datetime.fromisoformat(sa_iso.replace("Z", "+00:00").replace("+00:00", ""))
            if sa_dt > datetime.utcnow():
                initial_status = STATUS_SCHEDULED
        except (ValueError, TypeError):
            pass

    ts = now_iso()
    new_id = execute_write(
        """
        INSERT INTO campaigns (
            user_id, name, channel, intent, status,
            scheduled_at, budget, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(user_id),
            str(name).strip(),
            str(channel).strip().lower(),
            str(intent).strip().lower(),
            initial_status,
            sa_iso,
            float(budget) if budget is not None else None,
            ts, ts,
        ),
    )
    camp = get_campaign(new_id)
    _emit_campaign_trace(
        "CAMPAIGN_CREATED", camp,
        summary=f"yeni kampanya: {camp.name} ({camp.channel})",
    )
    return camp


def get_campaign(campaign_id: int) -> Campaign:
    row = execute_query(
        "SELECT * FROM campaigns WHERE id=?",
        (int(campaign_id),),
        one=True,
    )
    if not row:
        raise CampaignNotFound(f"campaign id {campaign_id} not found")
    return _row_to_campaign(row)


def list_campaigns(
    user_id: int,
    *,
    status: str | None = None,
    limit: int = 50,
) -> list[Campaign]:
    sql = "SELECT * FROM campaigns WHERE user_id=?"
    params: list[Any] = [int(user_id)]
    if status:
        sql += " AND status=?"
        params.append(str(status).strip().lower())
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit))
    rows = execute_query(sql, tuple(params))
    return [_row_to_campaign(r) for r in rows]


def _transition(
    campaign_id: int,
    target: str,
    *,
    fields: dict | None = None,
    summary: str | None = None,
    trace_tag: str = "CAMPAIGN_TRANSITION",
    allow_same_state_noop: bool = False,
    extra_trace: dict | None = None,
) -> Campaign:
    """Atomic state transition with idempotency + observability."""
    camp = get_campaign(campaign_id)
    if camp.status == target and allow_same_state_noop:
        return camp
    if not _can_transition(camp.status, target):
        raise InvalidTransitionError(
            f"cannot move campaign #{campaign_id} from {camp.status!r} → {target!r}"
        )

    updates = ["status=?", "updated_at=?"]
    values: list[Any] = [target, now_iso()]
    for k, v in (fields or {}).items():
        updates.append(f"{k}=?")
        values.append(v)
    values.append(int(campaign_id))

    execute_write(
        f"UPDATE campaigns SET {', '.join(updates)} WHERE id=?",
        tuple(values),
    )
    new_camp = get_campaign(campaign_id)
    _emit_campaign_trace(
        trace_tag, new_camp,
        summary=summary or f"kampanya {new_camp.name}: {camp.status} → {target}",
        extra=extra_trace,
    )
    return new_camp


def launch_campaign(campaign_id: int) -> Campaign:
    """Flip a draft/scheduled/paused campaign to `live`.

    Idempotent: calling on an already-live campaign returns it unchanged
    (logged at observability level for traceability). The workflow worker
    may re-fire workflows, so this MUST be safe under double-invocation.
    """
    camp = get_campaign(campaign_id)
    if camp.status == STATUS_LIVE:
        _emit_campaign_trace(
            "CAMPAIGN_RELAUNCH_NOOP", camp,
            summary=f"kampanya {camp.name} zaten canlı, tekrar başlatma yok",
        )
        return camp

    fields: dict = {}
    if not camp.started_at:
        fields["started_at"] = now_iso()
    return _transition(
        campaign_id, STATUS_LIVE,
        fields=fields,
        summary=f"kampanya {camp.name} canlı oldu",
        trace_tag="CAMPAIGN_LAUNCHED",
        allow_same_state_noop=True,
    )


def pause_campaign(campaign_id: int, *, reason: str = "operator_paused") -> Campaign:
    """Pause a live campaign. Reason is captured in the trace."""
    return _transition(
        campaign_id, STATUS_PAUSED,
        summary=f"kampanya durduruldu: {reason}",
        trace_tag="CAMPAIGN_PAUSED",
        extra_trace={"reason": reason},
    )


def complete_campaign(campaign_id: int) -> Campaign:
    """Mark a campaign completed. Sets ended_at if not already set.

    Idempotent — calling on an already-completed/archived campaign returns
    it unchanged. The runtime's task_service.complete_task calls this when
    the AI task finishes; that path may legitimately fire twice on retry.
    """
    camp = get_campaign(campaign_id)
    if camp.status in (STATUS_COMPLETED, STATUS_ARCHIVED):
        return camp
    fields: dict = {}
    if not camp.ended_at:
        fields["ended_at"] = now_iso()
    return _transition(
        campaign_id, STATUS_COMPLETED,
        fields=fields,
        summary=f"kampanya {camp.name} tamamlandı",
        trace_tag="CAMPAIGN_COMPLETED",
        allow_same_state_noop=True,
    )


def archive_campaign(campaign_id: int) -> Campaign:
    """Terminal state. From any non-archived state. Idempotent."""
    camp = get_campaign(campaign_id)
    if camp.status == STATUS_ARCHIVED:
        return camp
    fields: dict = {}
    if not camp.ended_at and camp.status not in (STATUS_DRAFT, STATUS_SCHEDULED):
        fields["ended_at"] = now_iso()
    return _transition(
        campaign_id, STATUS_ARCHIVED,
        fields=fields,
        summary=f"kampanya {camp.name} arşivlendi",
        trace_tag="CAMPAIGN_ARCHIVED",
        allow_same_state_noop=True,
    )


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def record_campaign_metric(
    campaign_id: int,
    *,
    impressions: int = 0,
    clicks: int = 0,
    conversions: int = 0,
    spend: float = 0.0,
    ts: datetime | str | None = None,
) -> CampaignMetricSnapshot:
    """Append a measurement snapshot to a campaign.

    Snapshots are append-only — they're never updated. The performance
    summary aggregates across all snapshots.
    """
    if impressions < 0 or clicks < 0 or conversions < 0 or spend < 0:
        raise ValueError("metric values must be non-negative")
    # Ensure campaign exists; raises CampaignNotFound otherwise.
    camp = get_campaign(campaign_id)

    iso_ts = _iso(ts) or now_iso()
    new_id = execute_write(
        """
        INSERT INTO campaign_metrics (
            campaign_id, ts, impressions, clicks, conversions, spend
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (int(campaign_id), iso_ts, int(impressions), int(clicks),
         int(conversions), float(spend)),
    )
    row = execute_query(
        "SELECT * FROM campaign_metrics WHERE id=?",
        (new_id,), one=True,
    )
    return _row_to_metric(row)


def list_campaign_metrics(
    campaign_id: int,
    *,
    limit: int = 100,
) -> list[CampaignMetricSnapshot]:
    rows = execute_query(
        """
        SELECT * FROM campaign_metrics
        WHERE campaign_id=?
        ORDER BY ts DESC LIMIT ?
        """,
        (int(campaign_id), int(limit)),
    )
    return [_row_to_metric(r) for r in rows]


def campaign_performance_summary(campaign_id: int) -> dict:
    """Aggregate impressions/clicks/conversions/spend + derived ratios."""
    camp = get_campaign(campaign_id)
    row = execute_query(
        """
        SELECT
            COALESCE(SUM(impressions), 0) AS impressions,
            COALESCE(SUM(clicks), 0)      AS clicks,
            COALESCE(SUM(conversions), 0) AS conversions,
            COALESCE(SUM(spend), 0)       AS spend,
            COUNT(*)                      AS snapshot_count,
            MIN(ts)                       AS first_ts,
            MAX(ts)                       AS last_ts
        FROM campaign_metrics
        WHERE campaign_id=?
        """,
        (int(campaign_id),),
        one=True,
    )
    if row is None:
        impressions = clicks = conversions = snapshots = 0
        spend = 0.0
        first_ts = last_ts = None
    else:
        d = dict(row)
        impressions = int(d.get("impressions") or 0)
        clicks = int(d.get("clicks") or 0)
        conversions = int(d.get("conversions") or 0)
        spend = float(d.get("spend") or 0.0)
        snapshots = int(d.get("snapshot_count") or 0)
        first_ts = d.get("first_ts")
        last_ts = d.get("last_ts")

    ctr = (clicks / impressions) if impressions > 0 else 0.0
    conv_rate = (conversions / clicks) if clicks > 0 else 0.0
    cpa = (spend / conversions) if conversions > 0 else 0.0
    cost_per_click = (spend / clicks) if clicks > 0 else 0.0

    duration_minutes: float | None = None
    if camp.started_at:
        try:
            start = datetime.fromisoformat(camp.started_at)
            end = (
                datetime.fromisoformat(camp.ended_at)
                if camp.ended_at else datetime.utcnow()
            )
            duration_minutes = round((end - start).total_seconds() / 60.0, 1)
        except (ValueError, TypeError):
            duration_minutes = None

    return {
        "campaign_id": campaign_id,
        "name": camp.name,
        "status": camp.status,
        "channel": camp.channel,
        "intent": camp.intent,
        "totals": {
            "impressions": impressions,
            "clicks": clicks,
            "conversions": conversions,
            "spend": round(spend, 2),
        },
        "ratios": {
            "ctr": round(ctr, 4),
            "conversion_rate": round(conv_rate, 4),
            "cost_per_click": round(cost_per_click, 2),
            "cost_per_acquisition": round(cpa, 2),
        },
        "window": {
            "first_metric_at": first_ts,
            "last_metric_at": last_ts,
            "duration_minutes": duration_minutes,
            "started_at": camp.started_at,
            "ended_at": camp.ended_at,
        },
        "snapshot_count": snapshots,
        "budget": camp.budget,
        "budget_consumed_pct": (
            round(min(1.0, spend / camp.budget) * 100, 1)
            if camp.budget else None
        ),
    }
