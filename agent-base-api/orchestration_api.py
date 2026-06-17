"""Internal orchestration API for AI Business Operating System dashboard."""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db

from agent_registry import list_agents
from approval_service import (
    approve,
    edit_proposal,
    get_pending_approvals,
    reject,
    retry_approval,
    submit_feedback,
)
from autonomous_planner import preview_plan_from_natural_language
from business_intelligence import get_insights_for_api
from business_state import build_business_state
from db import DEFAULT_USER_ID, execute_query, init_db
from planner_memory import get_memory_summary_for_api
from planner_runtime import apply_approved_proposal
from rule_service import (
    delete_rule,
    export_to_file,
    get_cache_stats,
    import_from_file,
    invalidate_cache,
    list_rules,
    set_rule_enabled,
)
from timeline_service import fetch_timeline
from tool_registry import get_registry_for_api

init_db()

router = APIRouter(prefix="/api/internal", tags=["internal-orchestration"])


class AutonomousPreviewRequest(BaseModel):
    natural_language: str
    user_id: int = DEFAULT_USER_ID


class ApprovalFeedbackRequest(BaseModel):
    feedback: str


class ApprovalEditRequest(BaseModel):
    proposal: dict


def _rows_to_list(rows):
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Multi-tenant auth context (Phase E)
# ---------------------------------------------------------------------------
#
# Every request can carry one of:
#   - X-API-Key header (resolves an org)
#   - user_id query param (legacy single-user mode — default behavior)
#
# The dependency below returns an AuthContext dict that endpoints can read
# from. Endpoints that want org-wide isolation (workflows/tasks/approvals)
# accept an `org_id` query override AND consult auth.org_id; if either is
# set, they widen the filter to all members of the org. Otherwise they
# keep filtering by user_id alone (test-mode preserved).


def get_current_auth(
    request: Request,
    user_id: int = Query(DEFAULT_USER_ID),
) -> dict:
    """Resolve the auth context for this request.

    The header path is the production path; the query param is the
    test-mode path. We DO NOT auto-resolve user → org here, because the
    user explicitly asked us to preserve the legacy `user_id=1` test
    behavior. Endpoints that want org filtering must read auth.org_id
    or accept an explicit org_id query param.
    """
    from auth_service import resolve_auth_from_request

    api_key = request.headers.get("X-API-Key") or request.headers.get("x-api-key")
    return resolve_auth_from_request(
        api_key_header=api_key,
        user_id_fallback=user_id,
    )


def _resolve_effective_org_id(
    auth: dict,
    explicit_org_id: int | None,
) -> int | None:
    """Return the org_id we should filter by, or None for legacy behavior.

    Order:
        1. Explicit query param (operator override).
        2. API key context (auth.org_id from X-API-Key).
        3. None → legacy single-user filter.
    """
    if explicit_org_id is not None:
        return int(explicit_org_id)
    if auth.get("org_id") is not None:
        return int(auth["org_id"])
    return None


def _org_user_ids_or_fallback(org_id: int | None, fallback_user_id: int) -> list[int]:
    """If org_id is set, return all org member user_ids. Else [fallback]."""
    if org_id is None:
        return [int(fallback_user_id)]
    from auth_service import get_org_user_ids
    ids = get_org_user_ids(org_id)
    return ids if ids else [int(fallback_user_id)]


@router.get("/dashboard")
async def serve_dashboard():
    return FileResponse(
        "index.html",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@router.get("/rules")
async def get_rules(
    user_id: int = Query(DEFAULT_USER_ID),
    include_disabled: bool = Query(False),
):
    rules = list_rules(user_id=user_id, include_disabled=include_disabled)
    cache = get_cache_stats()
    return {"data": rules, "user_id": user_id, "cache": cache}


@router.post("/rules/preview-autonomous")
async def preview_autonomous(body: AutonomousPreviewRequest):
    plan = preview_plan_from_natural_language(
        body.natural_language, user_id=body.user_id
    )
    return {"success": True, "plan": plan}


@router.delete("/rules/{rule_id}")
async def remove_rule(rule_id: int, user_id: int = Query(DEFAULT_USER_ID)):
    if not delete_rule(rule_id, user_id=user_id):
        raise HTTPException(404, "Rule not found")
    invalidate_cache(user_id)
    return {"success": True, "rule_id": rule_id}


@router.patch("/rules/{rule_id}/enabled")
async def toggle_rule(rule_id: int, enabled: bool = Query(...)):
    if not set_rule_enabled(rule_id, enabled):
        raise HTTPException(404, "Rule not found")
    return {"success": True, "rule_id": rule_id, "enabled": enabled}


@router.post("/rules/import-file")
async def import_rules_file(user_id: int = Query(DEFAULT_USER_ID)):
    count = import_from_file(user_id=user_id)
    return {"imported": count, "user_id": user_id}


@router.post("/rules/export-file")
async def export_rules_file(user_id: int = Query(DEFAULT_USER_ID)):
    content = export_to_file(user_id=user_id)
    return {"exported": True, "content": content}


@router.get("/workflows")
async def get_workflows(
    user_id: int = Query(DEFAULT_USER_ID),
    status: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    org_id: Optional[int] = Query(None),
    auth: dict = Depends(get_current_auth),
):
    effective_org_id = _resolve_effective_org_id(auth, org_id)
    user_ids = _org_user_ids_or_fallback(effective_org_id, user_id)
    placeholders = ",".join("?" * len(user_ids))
    if status:
        rows = execute_query(
            f"""
            SELECT * FROM workflow_instances
            WHERE user_id IN ({placeholders}) AND status=?
            ORDER BY id DESC LIMIT ?
            """,
            (*user_ids, status, limit),
        )
    else:
        rows = execute_query(
            f"""
            SELECT * FROM workflow_instances
            WHERE user_id IN ({placeholders})
            ORDER BY id DESC LIMIT ?
            """,
            (*user_ids, limit),
        )
    data = []
    for r in rows:
        d = dict(r)
        if d.get("metadata"):
            try:
                d["metadata"] = json.loads(d["metadata"])
            except json.JSONDecodeError:
                pass
        data.append(d)
    return {"data": data, "org_id": effective_org_id}


@router.get("/items")
async def get_items(
    user_id: int = Query(DEFAULT_USER_ID),
    limit: int = Query(100, le=200),
):
    """Mağaza ürünlerinin ham listesi — trending filtresi olmadan."""
    rows = execute_query(
        """
        SELECT i.id, i.name, i.price, i.stock, i.sales, i.category, i.status,
               i.store_id, s.name AS store_name
        FROM items i
        LEFT JOIN stores s ON s.id = i.store_id
        WHERE i.user_id = ?
        ORDER BY i.id DESC
        LIMIT ?
        """,
        (user_id, limit),
    )
    return {"data": _rows_to_list(rows)}


@router.get("/tasks")
async def get_tasks(
    user_id: int = Query(DEFAULT_USER_ID),
    status: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    org_id: Optional[int] = Query(None),
    auth: dict = Depends(get_current_auth),
):
    effective_org_id = _resolve_effective_org_id(auth, org_id)
    user_ids = _org_user_ids_or_fallback(effective_org_id, user_id)
    placeholders = ",".join("?" * len(user_ids))
    if status:
        rows = execute_query(
            f"""
            SELECT * FROM ai_tasks
            WHERE user_id IN ({placeholders}) AND status=?
            ORDER BY id DESC LIMIT ?
            """,
            (*user_ids, status, limit),
        )
    else:
        rows = execute_query(
            f"""
            SELECT * FROM ai_tasks
            WHERE user_id IN ({placeholders})
            ORDER BY id DESC LIMIT ?
            """,
            (*user_ids, limit),
        )
    return {"data": _rows_to_list(rows), "org_id": effective_org_id}


@router.get("/tool-executions")
async def get_tool_executions(
    user_id: int = Query(DEFAULT_USER_ID),
    limit: int = Query(50, le=200),
):
    rows = execute_query(
        """
        SELECT te.*, t.task_type, t.user_id
        FROM tool_executions te
        LEFT JOIN ai_tasks t ON t.id = te.task_id
        WHERE t.user_id = ?
        ORDER BY te.id DESC LIMIT ?
        """,
        (user_id, limit),
    )
    return {"data": _rows_to_list(rows)}


@router.get("/automation-logs")
async def get_automation_logs(
    user_id: int = Query(DEFAULT_USER_ID),
    limit: int = Query(50, le=200),
):
    rows = execute_query(
        """
        SELECT * FROM automation_logs
        WHERE user_id=?
        ORDER BY id DESC LIMIT ?
        """,
        (user_id, limit),
    )
    return {"data": _rows_to_list(rows)}


@router.get("/timeline")
async def get_timeline(
    cursor: int = Query(0),
    limit: int = Query(20, le=100),
    direction: str = Query("desc"),
    user_id: Optional[int] = Query(None),
    log_group: Optional[str] = Query(None),
):
    return fetch_timeline(
        cursor=cursor,
        direction=direction,
        limit=limit,
        user_id=user_id,
        log_group=log_group,
    )


@router.get("/proposals")
async def get_proposals(
    user_id: int = Query(DEFAULT_USER_ID),
    limit: int = Query(30, le=100),
):
    rows = execute_query(
        """
        SELECT * FROM planner_proposals
        WHERE user_id=?
        ORDER BY id DESC LIMIT ?
        """,
        (user_id, limit),
    )
    data = []
    for r in rows:
        d = dict(r)
        d["proposal"] = json.loads(d.pop("proposal_json", "{}") or "{}")
        data.append(d)
    return {"data": data}


@router.get("/approvals")
async def get_approvals(
    user_id: int = Query(DEFAULT_USER_ID),
    org_id: Optional[int] = Query(None),
    approval_type: Optional[str] = Query(None, description="post_approval / story_approval / banner_approval / campaign_approval / generic_approval"),
    auth: dict = Depends(get_current_auth),
):
    effective_org_id = _resolve_effective_org_id(auth, org_id)
    if effective_org_id is None:
        # Legacy single-user path
        return {
            "data": get_pending_approvals(user_id, approval_type=approval_type),
            "org_id": None,
        }
    # Org-wide path: pull pending approvals for every user in the org.
    user_ids = _org_user_ids_or_fallback(effective_org_id, user_id)
    aggregated: list[dict] = []
    for uid in user_ids:
        aggregated.extend(get_pending_approvals(uid, approval_type=approval_type))
    aggregated.sort(key=lambda r: r.get("id") or 0, reverse=True)
    return {"data": aggregated, "org_id": effective_org_id}


@router.get("/approvals/types")
async def get_approval_types(
    user_id: int = Query(DEFAULT_USER_ID),
    org_id: Optional[int] = Query(None),
    auth: dict = Depends(get_current_auth),
):
    """approval_type'lara göre sayı özeti. PHP UI dinamik sekme üretir."""
    from approval_service import get_approval_type_summary
    effective_org_id = _resolve_effective_org_id(auth, org_id)
    if effective_org_id is None:
        types = get_approval_type_summary(user_id)
    else:
        # Org-wide: tüm user'ları birleştir
        user_ids = _org_user_ids_or_fallback(effective_org_id, user_id)
        merged: dict[str, dict] = {}
        for uid in user_ids:
            for row in get_approval_type_summary(uid):
                k = row["approval_type"]
                if k not in merged:
                    merged[k] = {"approval_type": k, "total": 0, "pending": 0,
                                 "approved": 0, "rejected": 0}
                merged[k]["total"]    += row.get("total")    or 0
                merged[k]["pending"]  += row.get("pending")  or 0
                merged[k]["approved"] += row.get("approved") or 0
                merged[k]["rejected"] += row.get("rejected") or 0
        types = sorted(merged.values(),
                       key=lambda r: (r["pending"], r["total"]),
                       reverse=True)
    # UI etiket sözlüğü
    labels = {
        "post_approval":     "Post Onayları",
        "story_approval":    "Hikaye Onayları",
        "banner_approval":   "Banner Onayları",
        "campaign_approval": "Kampanya Onayları",
        "generic_approval":  "Genel Onaylar",
    }
    for t in types:
        atype = t.get("approval_type") or "generic_approval"
        t["label"] = labels.get(atype, atype.replace("_", " ").title())
    return {"types": types}


@router.post("/approvals/{approval_id}/approve")
async def approval_approve(approval_id: int):
    result = apply_approved_proposal(approval_id)
    return result


@router.post("/approvals/{approval_id}/reject")
async def approval_reject(approval_id: int, body: ApprovalFeedbackRequest):
    return reject(approval_id, feedback=body.feedback)


@router.post("/approvals/{approval_id}/edit")
async def approval_edit(approval_id: int, body: ApprovalEditRequest):
    return edit_proposal(approval_id, body.proposal)


@router.post("/approvals/{approval_id}/retry")
async def approval_retry(approval_id: int):
    return retry_approval(approval_id)


@router.post("/approvals/{approval_id}/feedback")
async def approval_feedback(approval_id: int, body: ApprovalFeedbackRequest):
    return submit_feedback(approval_id, body.feedback)


@router.get("/business-insights")
async def business_insights(user_id: int = Query(DEFAULT_USER_ID)):
    return {
        "insights": get_insights_for_api(user_id),
        "state": build_business_state(user_id),
    }


@router.get("/business-state")
async def business_state(user_id: int = Query(DEFAULT_USER_ID)):
    return build_business_state(user_id)


@router.get("/planner-memory")
async def planner_memory(user_id: int = Query(DEFAULT_USER_ID)):
    return {"data": get_memory_summary_for_api(user_id)}


@router.get("/tools/registry")
async def tools_registry():
    return {"data": get_registry_for_api()}


@router.get("/agents")
async def agents_list():
    return {"data": list_agents()}


@router.get("/cache-stats")
async def cache_stats():
    return get_cache_stats()


@router.get("/traces")
async def get_traces(
    user_id: int = Query(DEFAULT_USER_ID),
    tag: Optional[str] = Query(None),
    event_id: Optional[int] = Query(None),
    limit: int = Query(50, le=200),
):
    """AI reasoning feed for the dashboard."""
    from observability import fetch_traces

    return {"data": fetch_traces(
        user_id=user_id, trace_tag=tag, event_id=event_id, limit=limit
    )}


@router.get("/traces/tags")
async def get_trace_tags():
    from observability import fetch_trace_tags

    return {"data": fetch_trace_tags()}


@router.get("/traces/by-event/{event_id}")
async def get_traces_by_event(event_id: int, limit: int = Query(50, le=200)):
    from observability import fetch_traces

    return {"data": fetch_traces(event_id=event_id, limit=limit)}


@router.get("/humanized-timeline")
async def get_humanized_timeline(
    user_id: int = Query(DEFAULT_USER_ID),
    limit: int = Query(30, le=100),
):
    """Operator narrative timeline — never raw event dumps."""
    from business_retrieval_service import humanized_timeline
    return humanized_timeline(user_id=user_id, limit=limit)


@router.get("/insight-cards")
async def get_insight_cards(user_id: int = Query(DEFAULT_USER_ID)):
    """BI + cross-event hypotheses as operator-facing cards."""
    from business_retrieval_service import insight_cards
    return {"data": insight_cards(user_id=user_id)}


@router.get("/memory-patterns")
async def get_memory_patterns(user_id: int = Query(DEFAULT_USER_ID)):
    from business_retrieval_service import memory_patterns
    return memory_patterns(user_id=user_id)


@router.get("/operational-pressure")
async def get_operational_pressure(user_id: int = Query(DEFAULT_USER_ID)):
    from business_retrieval_service import operational_pressure
    return operational_pressure(user_id=user_id)


@router.get("/chat/intents")
async def get_chat_intents():
    """List of question intents the retrieval layer can answer."""
    from business_chat import supported_query_intents
    return {"data": supported_query_intents()}


# ---------------------------------------------------------------------------
# Calendar / scheduling — operator-facing
# ---------------------------------------------------------------------------


class ScheduleCreate(BaseModel):
    kind: str = "content_post"
    scheduled_at: str
    title: str = ""
    description: str = ""
    channel: Optional[str] = None
    workflow_name: Optional[str] = None
    payload: Optional[dict] = None
    recurrence: str = "once"
    requires_approval: bool = False
    user_id: int = DEFAULT_USER_ID


class ScheduleUpdate(BaseModel):
    scheduled_at: Optional[str] = None
    reason: Optional[str] = None


@router.get("/calendar")
async def get_calendar(
    start: str = Query(...),
    end: str = Query(...),
    user_id: int = Query(DEFAULT_USER_ID),
    include_executions: bool = Query(True, description="rule_executions completed publish'leri de döner"),
    include_approvals: bool = Query(True, description="post/story/banner/campaign approval_type'lı approved kayıtları döner"),
):
    """Operatör takvimi.

    Üç kaynak birleştirilir (her biri opsiyonel):
      1. scheduled_entries — operatör zamanlamaları + kural-tabanlı entry'ler
      2. rule_executions completed publish trace'leri (LangGraph publish_*)
      3. approval_requests approved + post/story/banner/campaign_approval
    """
    from scheduling_service import list_calendar_window
    base = list_calendar_window(user_id=user_id, start=start, end=end)
    if not isinstance(base, dict):
        return base

    # 2) Rule execution publish'leri
    extra_entries: list[dict] = []
    if include_executions:
        try:
            extra_entries.extend(_calendar_entries_from_executions(user_id, start, end))
        except Exception as exc:
            print(f"[calendar] execution scan failed: {exc}")

    # 3) Approved rule-tabanlı approval'lar
    if include_approvals:
        try:
            extra_entries.extend(_calendar_entries_from_approvals(user_id, start, end))
        except Exception as exc:
            print(f"[calendar] approval scan failed: {exc}")

    # Tarih bazında base.days'e yerleştir
    if extra_entries:
        day_index = {d["date"]: d for d in base.get("days", [])}
        for entry in extra_entries:
            date_key = (entry.get("scheduled_at") or "")[:10]
            if not date_key:
                continue
            if date_key not in day_index:
                bucket = {"date": date_key, "entries": []}
                base.setdefault("days", []).append(bucket)
                day_index[date_key] = bucket
            day_index[date_key]["entries"].append(entry)
        base["days"].sort(key=lambda d: d.get("date") or "")
    base["extra_count"] = len(extra_entries)
    return base


def _calendar_entries_from_executions(user_id: int, start: str, end: str) -> list[dict]:
    """rule_executions tamamlanan publish'leri takvim entry'sine çevir."""
    from db import execute_query
    rows = execute_query(
        """
        SELECT id, rule_id, status, started_at, ended_at, current_node, trace_summary
        FROM rule_executions
        WHERE user_id=?
          AND status IN ('completed','running','waiting_human','waiting_timer')
          AND COALESCE(ended_at, started_at) BETWEEN ? AND ?
        ORDER BY id DESC LIMIT 200
        """,
        (int(user_id), start + "T00:00:00", end + "T23:59:59"),
    )
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        out.append({
            "id": f"exec-{d['id']}",
            "source": "rule_execution",
            "execution_id": d["id"],
            "rule_id": d["rule_id"],
            "kind": "rule_publish",
            "status": d["status"],
            "title": f"Kural #{d['rule_id']} — {d['current_node'] or 'akış'}",
            "scheduled_at": d.get("ended_at") or d.get("started_at"),
            "summary": (d.get("trace_summary") or "")[:200],
        })
    return out


def _calendar_entries_from_approvals(user_id: int, start: str, end: str) -> list[dict]:
    """approval_requests post/story/banner/campaign'leri takvim entry'sine çevir."""
    from db import execute_query
    rows = execute_query(
        """
        SELECT id, approval_type, status, reason, workflow_name, created_at, updated_at
        FROM approval_requests
        WHERE user_id=?
          AND COALESCE(approval_type, 'generic_approval') IN
              ('post_approval','story_approval','banner_approval','campaign_approval')
          AND COALESCE(updated_at, created_at) BETWEEN ? AND ?
        ORDER BY id DESC LIMIT 200
        """,
        (int(user_id), start + "T00:00:00", end + "T23:59:59"),
    )
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        out.append({
            "id": f"approval-{d['id']}",
            "source": "approval",
            "approval_id": d["id"],
            "kind": d["approval_type"],
            "status": d["status"],
            "title": f"{d['approval_type']} #{d['id']}",
            "scheduled_at": d.get("updated_at") or d.get("created_at"),
            "summary": (d.get("reason") or "")[:200],
            "workflow_name": d.get("workflow_name"),
        })
    return out


@router.get("/schedules")
async def get_schedules(
    user_id: int = Query(DEFAULT_USER_ID),
    status: Optional[str] = Query(None),
    kind: Optional[str] = Query(None),
    limit: int = Query(100, le=500),
):
    from scheduling_service import list_schedules
    return {"data": list_schedules(
        user_id=user_id, status=status, kind=kind, limit=limit
    )}


@router.post("/schedules")
async def post_schedule(body: ScheduleCreate):
    from scheduling_service import create_schedule
    try:
        entry = create_schedule(
            user_id=body.user_id,
            kind=body.kind,
            scheduled_at=body.scheduled_at,
            title=body.title,
            description=body.description,
            channel=body.channel,
            workflow_name=body.workflow_name,
            payload=body.payload,
            recurrence=body.recurrence,
            requires_approval=body.requires_approval,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"data": entry}


@router.patch("/schedules/{schedule_id}")
async def patch_schedule(schedule_id: int, body: ScheduleUpdate):
    from scheduling_service import move_schedule
    if not body.scheduled_at:
        raise HTTPException(400, "scheduled_at required")
    try:
        entry = move_schedule(
            schedule_id, body.scheduled_at, reason=body.reason or "operator_reschedule"
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"data": entry}


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: int, reason: str = Query("operator_cancel")):
    from scheduling_service import cancel_schedule
    try:
        entry = cancel_schedule(schedule_id, reason=reason)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return {"data": entry}


@router.post("/schedules/fire-due")
async def post_fire_due():
    """Operator-callable fire-now (the worker normally handles this)."""
    from scheduling_service import fire_due_schedules
    return fire_due_schedules()


# ---------------------------------------------------------------------------
# Customer interaction center
# ---------------------------------------------------------------------------


class OpenThread(BaseModel):
    channel: str
    customer_ref: str
    initial_message: str
    customer_name: Optional[str] = None
    topic: Optional[str] = None
    user_id: int = DEFAULT_USER_ID


class IngestMessage(BaseModel):
    message: str
    from_role: str = "customer"


class EscalateBody(BaseModel):
    reason: str
    level: str = "manager"


@router.get("/customer-threads")
async def get_customer_threads(
    user_id: int = Query(DEFAULT_USER_ID),
    limit: int = Query(30, le=200),
):
    from customer_interaction_service import threads_overview
    return threads_overview(user_id=user_id, limit=limit)


@router.get("/customer-threads/{thread_id}")
async def get_customer_thread(thread_id: int):
    from customer_interaction_service import thread_summary
    out = thread_summary(thread_id)
    if not out:
        raise HTTPException(404, "thread not found")
    return out


@router.post("/customer-threads")
async def post_customer_thread(body: OpenThread):
    from customer_interaction_service import open_thread
    try:
        return {"data": open_thread(
            user_id=body.user_id,
            channel=body.channel,
            customer_ref=body.customer_ref,
            initial_message=body.initial_message,
            customer_name=body.customer_name,
            topic=body.topic,
        )}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/customer-threads/{thread_id}/messages")
async def post_customer_message(thread_id: int, body: IngestMessage):
    from customer_interaction_service import ingest_message
    try:
        return {"data": ingest_message(
            thread_id, message=body.message, from_role=body.from_role,
        )}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/customer-threads/{thread_id}/draft")
async def post_customer_draft(thread_id: int):
    from customer_interaction_service import draft_response
    try:
        return {"data": draft_response(thread_id, create_approval=True)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/customer-threads/{thread_id}/escalate")
async def post_customer_escalate(thread_id: int, body: EscalateBody):
    from customer_interaction_service import escalate
    try:
        return {"data": escalate(thread_id, reason=body.reason, level=body.level)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/customer-threads/{thread_id}/resolve")
async def post_customer_resolve(thread_id: int, reason: str = Query("resolved")):
    from customer_interaction_service import mark_resolved
    try:
        return {"data": mark_resolved(thread_id, reason=reason)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


# ---------------------------------------------------------------------------
# Social / commerce credentials
# ---------------------------------------------------------------------------
#
# WARNING — current /api/internal/* surface is unauthenticated (test mode).
# Anyone reaching this endpoint can list / save / revoke credentials for any
# user_id they specify. When auth lands (Phase E of the evolution plan), the
# request must be authenticated and `user_id` must come from the resolved
# session, not the request body. The encryption-at-rest guarantee is still
# valid even unauthenticated: tokens never appear in DB plaintext.


class SocialCredentialCreate(BaseModel):
    provider: str
    account_handle: str
    token: str
    scope: Optional[str] = None
    expires_at: Optional[str] = None
    user_id: int = DEFAULT_USER_ID


@router.get("/credentials")
async def get_credentials_list(user_id: int = Query(DEFAULT_USER_ID)):
    """List a user's connected social/commerce accounts. Never returns the token."""
    from social_credentials import has_secret_key, list_credentials
    return {
        "data": [c.as_public() for c in list_credentials(user_id)],
        "encryption_ready": has_secret_key(),
    }


@router.post("/credentials")
async def post_credential(body: SocialCredentialCreate):
    """Save (or refresh) an encrypted credential. Token is never persisted in plaintext."""
    from social_credentials import (
        MissingSecretKeyError,
        UnsupportedProviderError,
        save_credential,
    )
    try:
        cred = save_credential(
            user_id=body.user_id,
            provider=body.provider,
            account_handle=body.account_handle,
            token=body.token,
            scope=body.scope,
            expires_at=body.expires_at,
        )
    except MissingSecretKeyError as exc:
        raise HTTPException(503, f"encryption_unavailable: {exc}")
    except UnsupportedProviderError as exc:
        raise HTTPException(400, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"data": cred.as_public()}


@router.delete("/credentials/{cred_id}")
async def delete_credential(
    cred_id: int,
    user_id: int = Query(DEFAULT_USER_ID),
    reason: str = Query("operator_revoked"),
):
    """Revoke (soft-delete) a credential. Decryption key is NOT required."""
    from social_credentials import CredentialNotFound, revoke_credential
    try:
        cred = revoke_credential(cred_id, user_id=user_id, reason=reason)
    except CredentialNotFound as exc:
        raise HTTPException(404, str(exc))
    return {"data": cred.as_public()}


@router.get("/credentials/encryption-status")
async def get_credentials_encryption_status():
    """Quick check: is APP_SECRET_KEY usable?"""
    from social_credentials import SUPPORTED_PROVIDERS, has_secret_key
    return {
        "encryption_ready": has_secret_key(),
        "supported_providers": list(SUPPORTED_PROVIDERS),
    }


# ---------------------------------------------------------------------------
# Campaign lifecycle
# ---------------------------------------------------------------------------


class CampaignCreate(BaseModel):
    name: str
    channel: str
    intent: str
    scheduled_at: Optional[str] = None
    budget: Optional[float] = None
    user_id: int = DEFAULT_USER_ID


class CampaignPauseBody(BaseModel):
    reason: Optional[str] = "operator_paused"


@router.get("/campaigns")
async def get_campaigns(
    user_id: int = Query(DEFAULT_USER_ID),
    status: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
):
    from campaign_service import list_campaigns
    return {"data": [c.to_dict() for c in list_campaigns(
        user_id=user_id, status=status, limit=limit,
    )]}


@router.get("/campaigns/{campaign_id}")
async def get_campaign_detail(campaign_id: int):
    from campaign_service import CampaignNotFound, get_campaign
    try:
        camp = get_campaign(campaign_id)
    except CampaignNotFound as exc:
        raise HTTPException(404, str(exc))
    return {"data": camp.to_dict()}


@router.post("/campaigns")
async def post_campaign(body: CampaignCreate):
    from campaign_service import create_campaign
    try:
        camp = create_campaign(
            user_id=body.user_id,
            name=body.name,
            channel=body.channel,
            intent=body.intent,
            scheduled_at=body.scheduled_at,
            budget=body.budget,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"data": camp.to_dict()}


@router.patch("/campaigns/{campaign_id}/pause")
async def patch_campaign_pause(campaign_id: int, body: CampaignPauseBody | None = None):
    from campaign_service import (
        CampaignNotFound, InvalidTransitionError, pause_campaign,
    )
    reason = (body.reason if body else None) or "operator_paused"
    try:
        camp = pause_campaign(campaign_id, reason=reason)
    except CampaignNotFound as exc:
        raise HTTPException(404, str(exc))
    except InvalidTransitionError as exc:
        raise HTTPException(409, str(exc))
    return {"data": camp.to_dict()}


@router.patch("/campaigns/{campaign_id}/archive")
async def patch_campaign_archive(campaign_id: int):
    from campaign_service import CampaignNotFound, archive_campaign
    try:
        camp = archive_campaign(campaign_id)
    except CampaignNotFound as exc:
        raise HTTPException(404, str(exc))
    return {"data": camp.to_dict()}


@router.get("/campaigns/{campaign_id}/metrics")
async def get_campaign_metrics(
    campaign_id: int,
    limit: int = Query(100, le=500),
):
    from campaign_service import (
        CampaignNotFound, campaign_performance_summary, list_campaign_metrics,
    )
    try:
        summary = campaign_performance_summary(campaign_id)
    except CampaignNotFound as exc:
        raise HTTPException(404, str(exc))
    snapshots = list_campaign_metrics(campaign_id, limit=limit)
    return {
        "summary": summary,
        "snapshots": [s.to_dict() for s in snapshots],
    }


# ---------------------------------------------------------------------------
# Org + API key management
# ---------------------------------------------------------------------------


class OrgCreate(BaseModel):
    name: str
    owner_user_id: int = DEFAULT_USER_ID
    slug: Optional[str] = None
    plan: str = "free"


class MemberCreate(BaseModel):
    org_id: int
    user_id: int
    role: str = "viewer"


class ApiKeyCreate(BaseModel):
    org_id: int
    name: str
    scope: str = "read"
    expires_at: Optional[str] = None


@router.post("/orgs")
async def post_org(body: OrgCreate):
    from auth_service import (
        InvalidPlanError, InvalidRoleError, create_org,
    )
    try:
        org = create_org(
            name=body.name,
            owner_user_id=body.owner_user_id,
            slug=body.slug,
            plan=body.plan,
        )
    except (ValueError, InvalidPlanError, InvalidRoleError) as exc:
        raise HTTPException(400, str(exc))
    return {"data": org.to_dict()}


@router.get("/orgs/me")
async def get_my_org(
    user_id: int = Query(DEFAULT_USER_ID),
    auth: dict = Depends(get_current_auth),
):
    """Return the caller's primary org plus all orgs they belong to.

    Resolution order:
      1. auth.org_id (from X-API-Key) — the explicit production path.
      2. resolve_org_for_user(user_id) — picks oldest membership.

    If neither resolves, returns `org=None` and `memberships=[]` — this is
    the test-mode legacy state for users with zero memberships.
    """
    from auth_service import (
        OrgNotFound, get_member_role, get_org,
        list_orgs_for_user, resolve_org_for_user,
    )
    primary_org_id = auth.get("org_id") or resolve_org_for_user(user_id)
    primary = None
    role = None
    if primary_org_id is not None:
        try:
            primary = get_org(primary_org_id).to_dict()
            role = get_member_role(primary_org_id, user_id)
        except OrgNotFound:
            primary = None
    memberships = [o.to_dict() for o in list_orgs_for_user(user_id)]
    return {
        "user_id": user_id,
        "org": primary,
        "role": role,
        "memberships": memberships,
        "auth_source": auth.get("source"),
    }


@router.post("/orgs/members")
async def post_org_member(body: MemberCreate):
    from auth_service import (
        InvalidRoleError, MemberAlreadyExists, OrgNotFound, add_member,
    )
    try:
        member = add_member(body.org_id, body.user_id, body.role)
    except OrgNotFound as exc:
        raise HTTPException(404, str(exc))
    except InvalidRoleError as exc:
        raise HTTPException(400, str(exc))
    except MemberAlreadyExists as exc:
        raise HTTPException(409, str(exc))
    return {"data": member.to_dict()}


@router.get("/orgs/{org_id}/members")
async def get_org_members(org_id: int):
    from auth_service import OrgNotFound, get_org, list_members
    try:
        get_org(org_id)
    except OrgNotFound as exc:
        raise HTTPException(404, str(exc))
    return {"data": [m.to_dict() for m in list_members(org_id)]}


@router.get("/api-keys")
async def get_api_keys(
    org_id: Optional[int] = Query(None),
    auth: dict = Depends(get_current_auth),
):
    """List API keys for an org. Hash + raw value are NEVER returned."""
    from auth_service import list_api_keys

    effective_org_id = _resolve_effective_org_id(auth, org_id)
    if effective_org_id is None:
        raise HTTPException(
            400,
            "org_id is required (provide via ?org_id= or X-API-Key header)",
        )
    return {
        "data": [k.to_dict() for k in list_api_keys(effective_org_id)],
        "org_id": effective_org_id,
    }


@router.post("/api-keys")
async def post_api_key(body: ApiKeyCreate):
    """Issue a new API key. The raw key is returned ONCE in the response —
    the caller is responsible for surfacing it immediately and not
    persisting it anywhere recoverable.
    """
    from auth_service import (
        InvalidScopeError, OrgNotFound, create_api_key,
    )
    try:
        issued = create_api_key(
            body.org_id,
            body.name,
            scope=body.scope,
            expires_at=body.expires_at,
        )
    except OrgNotFound as exc:
        raise HTTPException(404, str(exc))
    except (ValueError, InvalidScopeError) as exc:
        raise HTTPException(400, str(exc))
    return {
        "data": issued.metadata.to_dict(),
        "raw_key": issued.raw_key,
        "warning": (
            "Bu anahtar yalnızca bu yanıtta görüntülenir; kaybedilirse "
            "yeniden oluşturulması gerekir."
        ),
    }


@router.delete("/api-keys/{api_key_id}")
async def delete_api_key(
    api_key_id: int,
    org_id: Optional[int] = Query(None),
    auth: dict = Depends(get_current_auth),
):
    """Soft-revoke an API key (status='revoked')."""
    from auth_service import revoke_api_key
    effective_org_id = _resolve_effective_org_id(auth, org_id)
    ok = revoke_api_key(api_key_id, org_id=effective_org_id)
    if not ok:
        raise HTTPException(404, "api key not found in this org")
    return {"success": True, "api_key_id": api_key_id}


# ---------------------------------------------------------------------------
# Structured rules (LangGraph)
# ---------------------------------------------------------------------------


class RuleParseRequest(BaseModel):
    natural_language: str
    name: Optional[str] = None
    user_id: int = DEFAULT_USER_ID


class RuleCreateRequest(BaseModel):
    natural_language: str
    name: Optional[str] = None
    user_id: int = DEFAULT_USER_ID
    enabled: bool = True


class RuleTestRequest(BaseModel):
    rule_id: Optional[int] = None
    natural_language: Optional[str] = None
    event_type: str = "store.created"
    event_payload: dict = {}
    user_id: int = DEFAULT_USER_ID


class RuleResumeRequest(BaseModel):
    decision: str = "approved"     # approved | rejected | edited
    feedback: Optional[str] = None
    edited_content: Optional[dict] = None
    decided_by: str = "operator"


def _rule_to_api(rule) -> dict:
    """StructuredRule → dashboard'a uygun JSON şekli."""
    from nl_rule_parser import explain_rule
    return {
        **rule.model_dump(mode="json"),
        "explanation": explain_rule(rule),
    }


@router.post("/structured-rules/parse")
async def parse_structured_rule(body: RuleParseRequest):
    """NL metni parse et — KAYIT YAPMAZ. Sadece önizleme döner."""
    from nl_rule_parser import explain_rule, parse_rule

    rule = parse_rule(
        body.natural_language,
        user_id=body.user_id,
        name_hint=body.name,
    )
    return {
        "rule": rule.model_dump(mode="json"),
        "explanation": explain_rule(rule),
        "parse_confidence": rule.parse_confidence,
        "missing_fields": rule.missing_fields,
    }


@router.post("/structured-rules")
async def create_structured_rule(body: RuleCreateRequest):
    """NL → parse + persist. Operatör için kuralı kaydeder."""
    from nl_rule_parser import parse_rule
    from structured_rule_engine import save_rule

    parsed = parse_rule(
        body.natural_language,
        user_id=body.user_id,
        name_hint=body.name,
    )
    parsed.enabled = body.enabled
    saved = save_rule(parsed)
    return {"data": _rule_to_api(saved)}


@router.get("/structured-rules")
async def list_structured_rules(
    user_id: int = Query(DEFAULT_USER_ID),
    enabled_only: bool = Query(False),
    limit: int = Query(100, le=500),
):
    from structured_rule_engine import list_rules
    rules = list_rules(user_id=user_id, enabled_only=enabled_only, limit=limit)
    return {"data": [_rule_to_api(r) for r in rules]}


@router.get("/structured-rules/{rule_id}")
async def get_structured_rule(rule_id: int):
    from structured_rule_engine import get_rule
    rule = get_rule(rule_id)
    if not rule:
        raise HTTPException(404, "rule not found")
    return {"data": _rule_to_api(rule)}


@router.patch("/structured-rules/{rule_id}/enabled")
async def toggle_structured_rule(rule_id: int, enabled: bool = Query(...)):
    from structured_rule_engine import get_rule, set_enabled
    ok = set_enabled(rule_id, enabled)
    if not ok:
        raise HTTPException(404, "rule not found")
    rule = get_rule(rule_id)
    return {"data": _rule_to_api(rule) if rule else None}


@router.delete("/structured-rules/{rule_id}")
async def delete_structured_rule(rule_id: int):
    from structured_rule_engine import delete_rule
    ok = delete_rule(rule_id)
    if not ok:
        raise HTTPException(404, "rule not found")
    return {"success": True, "rule_id": rule_id}


@router.post("/structured-rules/test")
async def test_structured_rule(body: RuleTestRequest):
    """Bir kuralı sentetik olayla dry-run et. Persistence yok."""
    from langgraph_engine.runtime import dry_run_preview
    from nl_rule_parser import parse_rule
    from structured_rule_engine import get_rule

    if body.rule_id:
        rule = get_rule(body.rule_id)
        if not rule:
            raise HTTPException(404, "rule not found")
    elif body.natural_language:
        rule = parse_rule(body.natural_language, user_id=body.user_id)
        rule.id = -1
    else:
        raise HTTPException(400, "rule_id veya natural_language gerekli")

    synthetic_event = {
        "event_id":   0,
        "event_type": body.event_type,
        "payload":    body.event_payload or {},
    }
    return dry_run_preview(rule, synthetic_event)


# ---- Executions ----


@router.get("/rule-executions")
async def list_rule_executions(
    user_id: int = Query(DEFAULT_USER_ID),
    status: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
):
    from langgraph_engine.runtime import list_executions
    return {"data": list_executions(user_id=user_id, status=status, limit=limit)}


@router.get("/rule-executions/{execution_id}")
async def get_rule_execution(execution_id: int):
    from langgraph_engine.runtime import get_execution, get_execution_traces
    row = get_execution(execution_id)
    if not row:
        raise HTTPException(404, "execution not found")
    traces = get_execution_traces(execution_id)
    return {"data": row, "traces": traces}


@router.post("/rule-executions/{execution_id}/resume")
async def resume_rule_execution(execution_id: int, body: RuleResumeRequest):
    from langgraph_engine.runtime import resume_execution
    try:
        result = resume_execution(
            execution_id,
            approval_decision=body.decision,
            feedback=body.feedback,
            edited_content=body.edited_content,
            decided_by=body.decided_by,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"data": result}


# ----- Tur 2: rule templates -----


class TemplateMaterialize(BaseModel):
    # template_id path'ten gelir; body'de tekrar zorunlu kılma
    parameters: dict = {}
    user_id: int = DEFAULT_USER_ID


@router.get("/rule-templates")
async def list_rule_templates(category: Optional[str] = Query(None)):
    from rule_templates import CATEGORY_LABELS, list_templates
    return {
        "data": list_templates(category),
        "categories": CATEGORY_LABELS,
    }


@router.post("/rule-templates/{template_id}/materialize")
async def materialize_rule_template(template_id: str, body: TemplateMaterialize):
    """Şablon + params'tan NL üret + parse önizlemesi döndür (kayıt yok)."""
    from nl_rule_parser import explain_rule, parse_rule
    from rule_templates import materialize
    try:
        materialized = materialize(template_id, body.parameters or {})
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    rule = parse_rule(materialized["natural_language"], user_id=body.user_id)
    return {
        "template": materialized,
        "rule": rule.model_dump(mode="json"),
        "explanation": explain_rule(rule),
        "parse_confidence": rule.parse_confidence,
    }


# ----- Tur 2: rule versions + conflicts -----


@router.get("/structured-rules/{rule_id}/versions")
async def get_rule_versions(rule_id: int):
    from structured_rule_engine import list_rule_versions
    return {"data": list_rule_versions(rule_id)}


@router.get("/structured-rules-conflicts")
async def get_rule_conflicts(user_id: int = Query(DEFAULT_USER_ID)):
    from structured_rule_engine import detect_conflicts
    return {"data": detect_conflicts(user_id)}


# ----- Tur 2: semantic entity resolver preview -----


class SemanticResolveRequest(BaseModel):
    query: str
    user_id: int = DEFAULT_USER_ID


@router.post("/semantic-resolve")
async def post_semantic_resolve(body: SemanticResolveRequest):
    from semantic_entity_resolver import explain_supported, resolve
    out = resolve(body.query)
    return {"data": out.to_dict(), "supported_patterns": explain_supported()}


# ----- Tur 2: adapter health -----


@router.get("/adapter-health")
async def adapter_health(user_id: int = Query(DEFAULT_USER_ID)):
    from tool_adapters import SOCIAL_PUBLISH_LIVE, get_adapter
    out = []
    for provider in ("instagram", "facebook", "tiktok"):
        adapter = get_adapter(provider)
        if adapter:
            out.append(adapter.health_check(user_id))
    return {"live_flag": SOCIAL_PUBLISH_LIVE, "adapters": out}


# ---------------------------------------------------------------------------
# Tur 3: rule learning + suggestions + conflicts + conversational edit
# ---------------------------------------------------------------------------


@router.get("/structured-rules/{rule_id}/learning")
async def get_rule_learning(rule_id: int):
    from rule_learning import get_rule_stats
    stats = get_rule_stats(rule_id)
    if not stats:
        raise HTTPException(404, "rule not found")
    return {"data": stats.to_dict()}


@router.get("/learning-suggestions")
async def get_learning_suggestions(
    user_id: int = Query(DEFAULT_USER_ID),
    limit: int = Query(20, le=100),
):
    from rule_learning import learning_suggestions, rules_health_overview
    return {
        "data": learning_suggestions(user_id, limit),
        "overview": rules_health_overview(user_id),
    }


@router.get("/structured-rules-conflicts/suggestions")
async def get_conflict_suggestions(user_id: int = Query(DEFAULT_USER_ID)):
    from conflict_resolver import conflicts_with_suggestions
    return {"data": conflicts_with_suggestions(user_id)}


class ConflictResolveRequest(BaseModel):
    conflict_key: str
    action: str   # 'deactivate_older' | 'deactivate_lower_health' | 'keep_one_review'
    user_id: int = DEFAULT_USER_ID


@router.post("/structured-rules-conflicts/resolve")
async def post_conflict_resolve(body: ConflictResolveRequest):
    from conflict_resolver import apply_resolution
    result = apply_resolution(body.conflict_key, body.action, user_id=body.user_id)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error") or "resolution failed")
    return {"data": result}


# ----- Conversational rule edit (dry-run preview + apply) -----


class ChatEditPreviewRequest(BaseModel):
    message: str
    user_id: int = DEFAULT_USER_ID
    session_id: Optional[str] = None


class ChatEditApplyRequest(BaseModel):
    rule_id: int
    kind: str
    params: dict = {}
    confirm_delete: bool = False
    user_id: int = DEFAULT_USER_ID


@router.post("/chat-edit/preview")
async def chat_edit_preview(body: ChatEditPreviewRequest):
    """Mesajı parse et — uygulamadan önce operatöre intent göster."""
    from conversational_rule_edit import detect_edit_intent
    from conversation_memory import get_active_rule, open_session

    active_rule_id = None
    if body.session_id:
        active_rule_id, _ = get_active_rule(body.session_id)
    elif body.session_id is None:
        # Yeni session aç ki get_active_rule çalışsın
        sess = open_session(body.user_id)
        body.session_id = sess["id"]

    intent = detect_edit_intent(
        body.message,
        user_id=body.user_id,
        session_active_rule_id=active_rule_id,
    )
    return {
        "data": intent.to_dict() if intent else None,
        "session_id": body.session_id,
    }


@router.post("/chat-edit/apply")
async def chat_edit_apply(body: ChatEditApplyRequest):
    """Operatör onayıyla edit uygula."""
    from conversational_rule_edit import EditIntent, apply_edit
    intent = EditIntent(
        kind=body.kind,
        target_rule_id=body.rule_id,
        params=body.params,
        confidence=1.0,
    )
    result = apply_edit(
        intent,
        user_id=body.user_id,
        confirm_delete=body.confirm_delete,
    )
    return {"data": result.to_dict()}


@router.get("/orchestration-health")
async def orchestration_health(user_id: int = Query(DEFAULT_USER_ID)):
    """Quick operational status for the AI Operations Center dashboard."""
    from tool_sandbox import circuit_state

    wf_active = execute_query(
        "SELECT COUNT(*) as c FROM workflow_instances WHERE user_id=? AND status IN ('scheduled','running')",
        (user_id,),
        one=True,
    )
    tasks_pending = execute_query(
        "SELECT COUNT(*) as c FROM ai_tasks WHERE user_id=? AND status IN ('pending','retrying')",
        (user_id,),
        one=True,
    )
    tasks_dead = execute_query(
        "SELECT COUNT(*) as c FROM ai_tasks WHERE user_id=? AND status='dead_letter'",
        (user_id,),
        one=True,
    )
    approvals = execute_query(
        "SELECT COUNT(*) as c FROM approval_requests WHERE user_id=? AND status='pending'",
        (user_id,),
        one=True,
    )
    recent_traces = execute_query(
        """
        SELECT trace_tag, COUNT(*) as c FROM orchestration_traces
        WHERE user_id=? AND created_at >= datetime('now', '-1 hour')
        GROUP BY trace_tag ORDER BY c DESC
        """,
        (user_id,),
    )
    return {
        "active_workflows": wf_active["c"] if wf_active else 0,
        "pending_tasks": tasks_pending["c"] if tasks_pending else 0,
        "dead_letter_tasks": tasks_dead["c"] if tasks_dead else 0,
        "pending_approvals": approvals["c"] if approvals else 0,
        "recent_trace_volume": [dict(r) for r in recent_traces],
        "tool_circuit": circuit_state(),
    }


@router.get("/users")
async def get_users():
    import os
    if os.environ.get("ALLOW_DEBUG_ENDPOINTS", "0") != "1":
        raise HTTPException(
            status_code=403,
            detail="debug endpoint disabled; set ALLOW_DEBUG_ENDPOINTS=1 to enable",
        )
    rows = execute_query(
        "SELECT id, name, email, created_at, updated_at FROM users ORDER BY id"
    )
    return {"data": _rows_to_list(rows)}


class ChatRequest(BaseModel):
    question: str
    user_id: int = DEFAULT_USER_ID
    session_id: Optional[str] = None


class ProductImportRequest(BaseModel):
    store_id: int = 1
    url: Optional[str] = None
    json_payload: Optional[str] = None


@router.post("/chat")
async def business_chat_endpoint(body: ChatRequest, db: Session = Depends(get_db)):
    """Chat ana endpoint'i.

    Yan-yazma: business_chat (eski SQLite chat_sessions/chat_turns) zekayı
    korumaya devam eder. Aynı turda yeni PG chat_sessions/chat_messages
    tablolarına da yazıyoruz — UI sidebar bunları okur.

    session_id: UI'dan UUID gelir; yoksa yeni session açılır.
    """
    from business_chat import answer_question
    from app.services.chat_session_service import (
        append_message, derive_legacy_id, ensure_session,
    )

    # 1) Yeni PG session'ı resolve/yarat
    new_sess = ensure_session(
        db,
        user_id=body.user_id,
        session_id=body.session_id,
        first_message=body.question,
    )

    # 2) Eski SQLite session_id türet (business_chat aynı UUID üstünden
    #    deterministik sess_<hex16> ile çalışır)
    legacy_sid = derive_legacy_id(new_sess.id)

    # 3) Asıl chat akışı (anti-rep, follow-up, conversational_rule_edit hep
    #    eski sistem üzerinden çalışır)
    result = answer_question(
        body.question, user_id=body.user_id, session_id=legacy_sid,
    )

    # 4) Yan-yazma: kullanıcı sorusu + asistan cevabı PG'ye
    try:
        append_message(db, session_id=new_sess.id, role="user", content=body.question)
        append_message(
            db, session_id=new_sess.id, role="assistant",
            content=str(result.get("answer") or ""),
        )
    except Exception as exc:
        # Yan-yazma hatası chat cevabını engellemesin
        print(f"[chat] PG yan-yazma hatası: {exc}")

    # 5) UI'a yeni PG UUID dön (eski legacy sess_xxx değil)
    result["session_id"] = str(new_sess.id)
    return result


@router.post("/chat/new-session")
async def business_chat_new_session(
    user_id: int = Query(DEFAULT_USER_ID),
    db: Session = Depends(get_db),
):
    """Yeni boş session aç — UI 'reset chat' butonu için."""
    from app.services.chat_session_service import ensure_session

    sess = ensure_session(db, user_id=user_id, session_id=None, first_message=None)
    return {
        "data": {
            "id": str(sess.id),
            "user_id": sess.user_id,
            "title": sess.title,
            "created_at": sess.created_at.isoformat() if sess.created_at else None,
            "last_message_at": sess.last_message_at.isoformat() if sess.last_message_at else None,
        }
    }


# ---------------------------------------------------------------------------
# Chat session yönetimi (UI sol panel için)
# ---------------------------------------------------------------------------


@router.get("/chat/sessions")
async def list_chat_sessions(
    user_id: int = Query(DEFAULT_USER_ID),
    limit: int = Query(200, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Kullanıcının chat session listesi — sidebar için (sıra: en yeni önce)."""
    from app.services.chat_session_service import list_sessions

    sessions = list_sessions(db, user_id=user_id, limit=limit)
    return {
        "data": [
            {
                "id": str(s.id),
                "title": s.title,
                "last_message_at": s.last_message_at.isoformat() if s.last_message_at else None,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in sessions
        ]
    }


@router.get("/chat/sessions/{session_id}")
async def get_chat_session(
    session_id: str,
    user_id: int = Query(DEFAULT_USER_ID),
    db: Session = Depends(get_db),
):
    """Session detayı + mesajlar (kronolojik)."""
    import uuid as _uuid
    from app.services.chat_session_service import get_session_with_messages

    try:
        sid = _uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Geçersiz session_id (UUID bekleniyor).")

    sess = get_session_with_messages(db, user_id=user_id, session_id=sid)
    if sess is None:
        raise HTTPException(status_code=404, detail="Session bulunamadı.")

    return {
        "data": {
            "id": str(sess.id),
            "title": sess.title,
            "last_message_at": sess.last_message_at.isoformat() if sess.last_message_at else None,
            "created_at": sess.created_at.isoformat() if sess.created_at else None,
            "messages": [
                {
                    "id": str(m.id),
                    "role": m.role,
                    "content": m.content,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                }
                for m in sess.messages
            ],
        }
    }


@router.delete("/chat/sessions/{session_id}")
async def delete_chat_session(
    session_id: str,
    user_id: int = Query(DEFAULT_USER_ID),
    db: Session = Depends(get_db),
):
    """Session sil — mesajlar CASCADE ile siler."""
    import uuid as _uuid
    from app.services.chat_session_service import delete_session

    try:
        sid = _uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Geçersiz session_id (UUID bekleniyor).")

    ok = delete_session(db, user_id=user_id, session_id=sid)
    if not ok:
        raise HTTPException(status_code=404, detail="Session bulunamadı.")
    return {"deleted": True}


@router.get("/trending-products")
async def trending_products(user_id: int = Query(DEFAULT_USER_ID)):
    from business_intelligence import detect_trending_products, generate_human_insights
    from business_state import build_business_state
    from cross_event_reasoner import reason_across_events

    state = build_business_state(user_id)
    cross = reason_across_events(user_id)
    insights = generate_human_insights(user_id, state, [], cross, {})
    return {
        "trending": detect_trending_products(user_id),
        "insights": insights,
    }


@router.post("/seed-data")
async def seed_data_endpoint(stores: int = Query(3)):
    from fake_data_generator import run_seed
    return run_seed(stores=stores)


@router.post("/products/import")
async def import_product_endpoint(body: ProductImportRequest):
    from product_import_service import (
        import_from_json_payload,
        simulate_import_from_url,
    )
    if body.url:
        return simulate_import_from_url(body.url, body.store_id)
    if body.json_payload:
        return import_from_json_payload(body.store_id, body.json_payload)
    raise HTTPException(400, "url veya json_payload gerekli")


@router.get("/dashboard-metrics")
async def dashboard_metrics(user_id: int = Query(DEFAULT_USER_ID)):
    """KPI cards for dashboard."""
    from business_intelligence import analyze_sales_trend, detect_trending_products
    from business_state import build_business_state

    state = build_business_state(user_id)
    sales = analyze_sales_trend(user_id)
    trending = detect_trending_products(user_id, 5)
    wfs = execute_query(
        "SELECT COUNT(*) as c FROM workflow_instances WHERE user_id=? AND status IN ('running','scheduled')",
        (user_id,),
        one=True,
    )
    return {
        "revenue_units": sales["total_sales"],
        "sales_trend": sales["trend"],
        "stock_health": state.get("inventory", {}).get("health"),
        "active_workflows": wfs["c"] if wfs else 0,
        "pending_approvals": state.get("engagement", {}).get("pending_approvals", 0),
        "trending_products": trending,
        "top_products": state.get("sales", {}).get("top_products", []),
    }
