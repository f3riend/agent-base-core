"""
Applies planner proposals through the deterministic workflow engine.

Autonomous planner proposals flow through approval + safety gates.
NEVER executes CrewAI tools directly.
"""

from __future__ import annotations

from env_bootstrap import load_app_env

load_app_env()

import json
import os

from approval_service import assess_approval_need, create_approval_request, get_approval
from db import get_db, init_db, now_iso
from observability import log_approval_required, log_workflow_latency, MetricTimer
from planner_memory import record_plan, update_outcome
from safety_service import record_execution, validate_autonomous_execution
from workflow_service import cancel_workflows, create_workflow

AUTO_APPLY = os.environ.get("AI_PLANNER_AUTO_APPLY", "1") == "1"
REQUIRE_APPROVAL = os.environ.get("AUTONOMOUS_REQUIRE_APPROVAL", "1") == "1"  # gate external publish only via approval_service


def save_proposal(
    event_id: int,
    event_name: str,
    entity_type: str,
    entity_id: int,
    proposal: dict,
    user_id: int = 1,
) -> int:
    init_db()
    conn = get_db()
    cursor = conn.execute(
        """
        INSERT INTO planner_proposals (
            event_id, event_name, entity_type, entity_id,
            proposal_json, applied, created_at, user_id
        )
        VALUES (?, ?, ?, ?, ?, 0, ?, ?)
        """,
        (
            event_id,
            event_name,
            entity_type.lower(),
            entity_id,
            json.dumps(proposal),
            now_iso(),
            user_id,
        ),
    )
    proposal_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return proposal_id


def mark_proposal_applied(proposal_id: int, result: str):
    conn = get_db()
    conn.execute(
        "UPDATE planner_proposals SET applied=1, apply_result=? WHERE id=?",
        (result, proposal_id),
    )
    conn.commit()
    conn.close()


def apply_proposal(
    proposal: dict,
    subject_type: str,
    subject_id: int,
    event_id: int = 0,
    proposal_id: int | None = None,
    user_id: int = 1,
    event: dict | None = None,
    event_name: str = "",
    skip_approval_check: bool = False,
) -> dict:
    """Apply plan via workflow engine only."""
    needs, risk, appr_reason = assess_approval_need(proposal)

    if needs and not skip_approval_check:
        approval_id = create_approval_request(
            user_id, proposal, proposal_id=proposal_id, event_id=event_id
        )
        if REQUIRE_APPROVAL or proposal.get("requires_approval"):
            result = f"stored:requires_approval#{approval_id}"
            if proposal_id:
                mark_proposal_applied(proposal_id, result)
            log_approval_required(
                approval_id, appr_reason, float(proposal.get("confidence", 0)), risk
            )
            return {
                "applied": False,
                "reason": result,
                "approval_id": approval_id,
                "proposal": proposal,
            }

    if not AUTO_APPLY:
        result = "stored_only:auto_apply_disabled"
        if proposal_id:
            mark_proposal_applied(proposal_id, result)
        return {"applied": False, "reason": result}

    decision = proposal.get("decision", "noop")
    workflow_name = proposal.get("workflow_name") or proposal.get("workflow")
    delay = int(proposal.get("delay", 0))
    entity_type = subject_type.lower()

    metadata = {
        "plan": proposal,
        "tools": proposal.get("tools", []),
        "task_type": proposal.get("task_type"),
        "task_payload": proposal.get("task_payload"),
        "requires_approval": proposal.get("requires_approval", False),
        "approved": skip_approval_check or not needs,
        "source": proposal.get("source", "planner"),
        "business_intent": proposal.get("business_intent"),
        "agent": proposal.get("agent"),
    }

    if decision == "noop":
        result = f"noop:{proposal.get('reason', '')}"
        if proposal_id:
            mark_proposal_applied(proposal_id, result)
        if event:
            record_plan(
                user_id, event, event_name, subject_type, subject_id,
                proposal, "noop",
            )
        return {"applied": False, "decision": "noop", "reason": proposal.get("reason")}

    if decision == "create_workflow":
        if not workflow_name:
            result = "error:missing_workflow_name"
            if proposal_id:
                mark_proposal_applied(proposal_id, result)
            return {"applied": False, "error": result}

        safe, safety_msg = validate_autonomous_execution(
            user_id, entity_type, subject_id, workflow_name
        )
        if not safe:
            result = f"blocked:safety:{safety_msg}"
            if proposal_id:
                mark_proposal_applied(proposal_id, result)
            return {"applied": False, "reason": result, "safety": safety_msg}

        timer = MetricTimer("workflow_create")
        workflow_id = create_workflow(
            workflow_name=workflow_name,
            entity_type=entity_type,
            entity_id=subject_id,
            delay_days=delay,
            event_id=event_id,
            user_id=user_id,
            metadata=metadata,
        )
        latency = timer.finish(workflow_id=workflow_id or 0, name=workflow_name)

        if workflow_id:
            is_campaign = "campaign" in workflow_name or "instagram" in workflow_name
            record_execution(
                user_id, entity_type, subject_id, workflow_name, is_campaign
            )
            log_workflow_latency(
                workflow_id, workflow_name, latency, "scheduled"
            )

        result = (
            f"create_workflow:{workflow_name}#{workflow_id}"
            if workflow_id
            else f"create_workflow_skipped:{workflow_name}"
        )
        if proposal_id:
            mark_proposal_applied(proposal_id, result)
        if event and workflow_id:
            update_outcome(
                _find_memory_id(user_id, event["id"]),
                "success" if workflow_id else "skipped",
            )
        print(f"[PLANNER RUNTIME] {result}")
        return {
            "applied": bool(workflow_id),
            "decision": "create_workflow",
            "workflow_id": workflow_id,
            "workflow_name": workflow_name,
            "proposal": proposal,
        }

    if decision == "cancel_workflow":
        if not workflow_name:
            result = "error:missing_workflow_name"
            if proposal_id:
                mark_proposal_applied(proposal_id, result)
            return {"applied": False, "error": result}

        cancel_workflows(
            entity_type=entity_type,
            entity_id=subject_id,
            reason=proposal.get("reason", "planner_cancelled"),
            workflow_name=workflow_name,
            event_id=event_id,
            user_id=user_id,
        )
        result = f"cancel_workflow:{workflow_name}"
        if proposal_id:
            mark_proposal_applied(proposal_id, result)
        return {"applied": True, "decision": "cancel_workflow", "workflow": workflow_name}

    result = f"unknown_decision:{decision}"
    if proposal_id:
        mark_proposal_applied(proposal_id, result)
    return {"applied": False, "error": result}


def apply_approved_proposal(approval_id: int) -> dict:
    """Apply after human approval from dashboard."""
    from approval_service import approve

    result = approve(approval_id)
    if not result.get("success"):
        return result

    req = get_approval(approval_id)
    if not req:
        return {"success": False, "error": "not_found"}

    proposal = result["proposal"]

    # --- LangGraph köprüsü (Bölüm 4) ---
    # Eğer proposal structured_rule execution'dan geldiyse, ilgili LangGraph
    # graph'ını resume_execution ile devam ettir. business_intent veya
    # entity_type ipucundan tespit ediliyor.
    task_payload = proposal.get("task_payload") or {}
    execution_id = task_payload.get("execution_id")
    is_rule_exec = (
        proposal.get("business_intent") == "structured_rule_execution"
        or proposal.get("entity_type") == "rule_execution"
    )
    if is_rule_exec and execution_id:
        try:
            from langgraph_engine.runtime import resume_execution
            lg_result = resume_execution(
                int(execution_id),
                approval_decision="approved",
                decided_by="dashboard_user",
            )
            return {
                "success": True,
                "applied": True,
                "kind": "langgraph_resume",
                "execution_id": int(execution_id),
                "langgraph_status": lg_result.get("status"),
                "current_node": lg_result.get("current_node"),
                "proposal": proposal,
            }
        except Exception as exc:
            return {
                "success": False,
                "error": f"langgraph_resume_failed: {exc}",
                "execution_id": int(execution_id),
                "proposal": proposal,
            }

    entity_type = (proposal.get("entity_type") or "store").lower()
    subject_type = entity_type.title() if entity_type != "item" else "Item"
    if entity_type == "store":
        subject_type = "Store"
    subject_id = proposal.get("entity_id") or proposal.get("task_payload", {}).get("entity_id", 1)

    return apply_proposal(
        proposal=proposal,
        subject_type=subject_type,
        subject_id=int(subject_id),
        event_id=req.get("event_id") or 0,
        user_id=req["user_id"],
        skip_approval_check=True,
    )


def _find_memory_id(user_id: int, event_id: int) -> int | None:
    conn = get_db()
    row = conn.execute(
        """
        SELECT id FROM planner_memory
        WHERE user_id=? AND event_id=?
        ORDER BY id DESC LIMIT 1
        """,
        (user_id, event_id),
    ).fetchone()
    conn.close()
    return row["id"] if row else None


def handle_autonomous_event(
    event: dict,
    event_name: str,
    context: dict,
    subject_type: str,
    subject_id: int,
    user_id: int,
) -> dict | None:
    from autonomous_planner import create_plan
    from event_router import routing_confidence, route_event
    from observability import log_routing_decision

    route = route_event(event_name, event)
    log_routing_decision(
        event_name, route, routing_confidence(event_name, event), "autonomous_path"
    )

    plan = create_plan(
        event=event,
        event_name=event_name,
        context=context,
        subject_type=subject_type,
        subject_id=subject_id,
        user_id=user_id,
    )

    if plan is None:
        return None

    proposal_id = save_proposal(
        event_id=event["id"],
        event_name=event_name,
        entity_type=subject_type,
        entity_id=subject_id,
        proposal=plan,
        user_id=user_id,
    )

    if plan["decision"] == "noop":
        mark_proposal_applied(proposal_id, f"noop:{plan.get('reason')}")
        return {"proposal": plan, "applied": False}

    return apply_proposal(
        proposal=plan,
        subject_type=subject_type,
        subject_id=subject_id,
        event_id=event["id"],
        proposal_id=proposal_id,
        user_id=user_id,
        event=event,
        event_name=event_name,
    )


def handle_critical_fallback(
    event: dict,
    event_name: str,
    context: dict,
    subject_type: str,
    subject_id: int,
    user_id: int,
) -> dict | None:
    from ai_planner import propose_action

    proposal = propose_action(
        event_name=event_name,
        context=context,
        subject_type=subject_type,
        subject_id=subject_id,
        event=event,
    )
    if proposal is None:
        return None

    proposal_id = save_proposal(
        event["id"], event_name, subject_type, subject_id, proposal, user_id
    )
    if proposal["decision"] == "noop":
        mark_proposal_applied(proposal_id, f"noop:{proposal.get('reason')}")
        return {"proposal": proposal, "applied": False}

    return apply_proposal(
        proposal=proposal,
        subject_type=subject_type,
        subject_id=subject_id,
        event_id=event["id"],
        proposal_id=proposal_id,
        user_id=user_id,
    )
