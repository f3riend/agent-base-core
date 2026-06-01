"""
Autonomous AI Planner — semantic business orchestration without ontology hardcoding.

Produces structured execution PLANS only. Never executes tools.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from typing import Any, Optional

from agent_registry import agent_context_for_plan
from business_intelligence import analyze as bi_analyze
from business_state import build_business_state, state_summary_for_planner
from event_router import is_critical_event, route_event
from observability import (
    log_ai_reasoning,
    log_autonomous_plan,
    log_business_signal,
    log_tool_selection,
)
from ontology import intent_for_insight
from planner_memory import (
    build_memory_context,
    get_automation_log_summary,
    get_workflow_history_summary,
    record_plan,
)
from rule_service import get_compiled_rules
from tool_registry import (
    build_registry_summary,
    get_tools_for_autonomous_plan,
    rank_tools_with_reasoning,
)

CONFIDENCE_AUTO_APPLY = float(
    os.environ.get("AUTONOMOUS_PLANNER_MIN_CONFIDENCE", "0.55")
)
APPROVAL_THRESHOLD = float(
    os.environ.get("AUTONOMOUS_PLANNER_APPROVAL_THRESHOLD", "0.72")
)


def _slugify_workflow(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")[:56] or "autonomous_campaign"


def _noop(reason: str, confidence: float = 1.0) -> dict:
    return {
        "decision": "noop",
        "workflow_name": None,
        "workflow": None,
        "reason": reason,
        "tools": [],
        "priority": "low",
        "confidence": confidence,
        "requires_approval": False,
        "source": "autonomous",
        "business_intent": "none",
    }


def _attach_campaign(plan: dict, ctx: dict) -> dict:
    """If the plan's business_intent triggers a campaign, create one and
    attach its id to plan + task_payload.

    Idempotent: skips entirely if plan already carries `campaign_id`, or if
    the decision is not `create_workflow`, or if campaign creation fails
    (the workflow still proceeds — campaign tracking is best-effort).

    A campaign is created in `draft` (or `scheduled` if the plan has a
    non-zero `delay`). `workflow_service._execute_autonomous_workflow`
    will flip it to `live` when the workflow fires.
    """
    try:
        from campaign_service import CAMPAIGN_TRIGGER_INTENTS, create_campaign
    except Exception as exc:
        print(f"[PLANNER] campaign_service unavailable: {exc}")
        return plan

    if plan.get("campaign_id"):
        return plan
    if plan.get("decision") != "create_workflow":
        return plan
    intent = plan.get("business_intent") or ""
    if intent not in CAMPAIGN_TRIGGER_INTENTS:
        return plan

    payload = plan.get("task_payload") or {}
    item = (ctx.get("item") or payload.get("item") or {}) or {}
    store = (ctx.get("store") or payload.get("store") or {}) or {}
    entity_name = (
        (item.get("name") if isinstance(item, dict) else None)
        or (store.get("name") if isinstance(store, dict) else None)
        or "—"
    )
    channel = payload.get("platform") or "instagram"

    delay_days = int(plan.get("delay") or 0)
    scheduled_at = None
    if delay_days > 0:
        scheduled_at = (datetime.utcnow() + timedelta(days=delay_days)).isoformat()

    user_id_int = int(ctx.get("user_id") or 1)
    name = f"{intent.replace('_', ' ').title()} — {entity_name}"[:80]

    try:
        camp = create_campaign(
            user_id=user_id_int,
            name=name,
            channel=str(channel),
            intent=intent,
            scheduled_at=scheduled_at,
        )
    except Exception as exc:
        # Campaign creation must never break planning. Log and continue.
        print(f"[PLANNER] campaign create failed for intent={intent}: {exc}")
        log_ai_reasoning(
            "campaign_create_failed",
            f"intent={intent} error={exc}",
            None,
            (ctx.get("event") or {}).get("id"),
            user_id_int,
            {"intent": intent},
        )
        return plan

    plan["campaign_id"] = camp.id
    plan["campaign_status"] = camp.status
    payload["campaign_id"] = camp.id
    plan["task_payload"] = payload

    log_ai_reasoning(
        "campaign_attached",
        f"{name} (id={camp.id}, status={camp.status})",
        plan.get("confidence"),
        (ctx.get("event") or {}).get("id"),
        user_id_int,
        {"campaign_id": camp.id, "intent": intent, "channel": channel},
    )
    return plan


def build_planner_context(
    event: dict,
    event_name: str,
    context: dict,
    subject_type: str,
    subject_id: int,
    user_id: int,
) -> dict:
    route = route_event(event_name, event)
    query_hint = json.dumps({
        **(event.get("payload") or {}),
        "desc": event.get("description"),
    }, default=str)

    memory = build_memory_context(
        user_id, subject_type.lower(), subject_id, query_hint=query_hint
    )
    rules = get_compiled_rules(user_id)
    bi = bi_analyze(event, event_name, context, user_id)
    biz_state = build_business_state(user_id)
    from cross_event_reasoner import reason_across_events

    cross = reason_across_events(user_id, current_event=event)

    return {
        "event": {
            "id": event.get("id"),
            "name": event_name,
            "description": event.get("description"),
            "group": event.get("group"),
            "payload": event.get("payload") or {},
            "changes": event.get("changes") or {},
        },
        "subject": {"type": subject_type, "id": subject_id},
        "user_id": user_id,
        "route": route,
        "store": context.get("store"),
        "item": context.get("item"),
        "order": context.get("order"),
        "tenant_rules": [
            {"name": r.get("name"), "when": r.get("when"), "workflow": r.get("workflow")}
            for r in rules
        ],
        "memory": memory,
        "business_intelligence": bi,
        "business_state": biz_state,
        "business_state_summary": state_summary_for_planner(biz_state),
        "automation_logs": get_automation_log_summary(user_id),
        "workflow_history": get_workflow_history_summary(
            user_id, subject_type.lower(), subject_id
        ),
        "available_tools": build_registry_summary(),
        "cross_event_reasoning": cross,
    }


def _intent_from_insights(bi: dict) -> tuple[str, float, str]:
    """Derive primary business intent from BI layer — sourced from ontology."""
    if not bi.get("insights"):
        return "general_marketing", 0.4, "Yeterli iş sinyali bulunamadı"

    top = bi.get("top_opportunity") or max(
        bi["insights"], key=lambda x: x["strength"]
    )
    intent = intent_for_insight(top["type"], default=top["type"])
    return intent, top["strength"], top["message"]


def _plan_from_context(ctx: dict) -> dict:
    """Contextual planning from BI + business state + memory."""
    user_id = int(ctx.get("user_id") or 1)
    bi = ctx["business_intelligence"]
    event = ctx["event"]
    item = ctx.get("item") or {}
    store = ctx.get("store") or {}
    route = ctx.get("route", "hybrid")

    if bi.get("has_critical") and route != "creative":
        critical_ins = [i for i in bi["insights"] if i.get("critical")]
        if critical_ins:
            log_business_signal(
                critical_ins[0]["type"],
                critical_ins[0]["strength"],
                "bi_critical",
            )
            return _noop(
                f"Kritik sinyal kural motoruna bırakıldı: {critical_ins[0]['message']}",
                0.9,
            )

    intent, strength, message = _intent_from_insights(bi)

    if strength < 0.35 and not bi.get("has_creative"):
        return _noop(message, strength)

    log_business_signal(intent, strength, "bi_inference", {"message": message})

    name_part = (item.get("name") or store.get("name") or "campaign")[:24]
    workflow_name = _slugify_workflow(f"{intent}_{name_part}")

    context_text = " ".join([
        message,
        bi.get("summary", ""),
        ctx.get("business_state_summary", ""),
        event.get("description") or "",
        json.dumps(event.get("payload") or {}, default=str),
    ])

    tool_result = rank_tools_with_reasoning(context_text, intent, limit=3)
    tools = tool_result["tools"]

    log_tool_selection(tools, tool_result["scores"], tool_result["reasoning"])

    confidence = min(0.95, 0.5 + strength * 0.4)
    if ctx["memory"].get("successful_campaigns"):
        confidence = min(0.95, confidence + 0.05)
    if ctx["memory"].get("failed_count", 0) > 2:
        confidence = max(0.4, confidence - 0.1)

    priority = "high" if strength > 0.75 else "medium"
    from planner_learning import get_confidence_adjustment

    confidence = min(0.95, confidence + get_confidence_adjustment(user_id, intent, tools))

    from approval_service import assess_approval_need

    needs_approval, _, _ = assess_approval_need({
        "tools": tools,
        "workflow_name": workflow_name,
        "business_intent": intent,
        "confidence": confidence,
    })
    requires_approval = needs_approval

    agent_ctx = agent_context_for_plan(route, intent)

    entity_type = "item" if item else "store"
    task_payload = {
        "goal": message,
        "business_intent": intent,
        "event": event,
        "tools": tools,
        "platform": "instagram",
        "bi_summary": bi.get("summary"),
        "agent": agent_ctx,
    }
    if item:
        task_payload["item"] = dict(item) if hasattr(item, "keys") else item
    if store:
        task_payload["store"] = dict(store) if hasattr(store, "keys") else store

    plan = {
        "decision": "create_workflow",
        "workflow_name": workflow_name,
        "workflow": workflow_name,
        "reason": message,
        "tools": tools,
        "priority": priority,
        "confidence": round(confidence, 3),
        "requires_approval": requires_approval,
        "source": "autonomous_contextual",
        "business_intent": intent,
        "delay": 0,
        "task_type": f"autonomous_{workflow_name[:40]}",
        "entity_type": entity_type,
        "entity_id": (ctx.get("subject") or {}).get("id", 0),
        "task_payload": task_payload,
        "reasoning": tool_result["reasoning"],
        "agent": agent_ctx,
        "bi_insights": [i["type"] for i in bi.get("insights", [])[:5]],
    }

    log_ai_reasoning(
        "plan_synthesis",
        message,
        confidence,
        event.get("id"),
        user_id,
        {"intent": intent, "tools": tools},
    )

    # If this plan is a campaign-class intent, attach a Campaign entity so
    # the workflow has a measurable lifecycle alongside it.
    plan = _attach_campaign(plan, ctx)
    return plan


def _plan_with_llm(ctx: dict) -> dict:
    """Native OpenAI chat completion ile plan üret.

    Tur 2'de CrewAI tamamen kaldırıldı — eski `_plan_with_crewai`
    Agent/Crew/Task katmanı sadece bir LLM çağrısının üzerine ekstra
    overhead getiriyordu. Şimdi doğrudan OpenAI'a strict JSON çıktısı
    isteyerek aynı işi yapıyoruz; plan_validator.parse_planner_output
    yine son güvenlik ağı.
    """
    from openai import OpenAI

    system_prompt = (
        "You are an autonomous business orchestration planner.\n"
        "Interpret business events and propose workflow plans. Never "
        "execute tools. Return structured JSON plans only.\n"
        "You understand e-commerce, marketing, inventory, and customer "
        "engagement in Turkish and English.\n"
        "Rules: NEVER execute tools directly. Use business_intelligence "
        "insights. requires_approval must be true when confidence < 0.72 "
        "or external publishing. workflow_name must be unique snake_case."
    )
    user_prompt = (
        "Analyze this business event and propose an orchestration plan.\n\n"
        "Context (includes BI insights and business state):\n"
        f"{json.dumps(ctx, indent=2, default=str)[:12000]}\n\n"
        'Return ONLY JSON with shape:\n'
        '{\n'
        '  "decision": "create_workflow" | "cancel_workflow" | "noop",\n'
        '  "workflow_name": "snake_case unique workflow id",\n'
        '  "reason": "business explanation in Turkish or English",\n'
        '  "tools": ["tool_name", ...],\n'
        '  "priority": "low" | "medium" | "high",\n'
        '  "confidence": 0.0-1.0,\n'
        '  "requires_approval": true/false,\n'
        '  "business_intent": "short intent label",\n'
        '  "delay": 0\n'
        '}'
    )

    client = OpenAI(timeout=float(os.environ.get("PLANNER_LLM_TIMEOUT", "20")))
    completion = client.chat.completions.create(
        model=os.environ.get("PLANNER_LLM_MODEL", "gpt-4o-mini"),
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
        max_tokens=600,
    )
    result = (completion.choices[0].message.content or "").strip()

    from plan_validator import parse_planner_output

    plan = parse_planner_output(result)
    if not plan:
        raise ValueError("Autonomous planner did not return parseable JSON")
    plan["source"] = "autonomous_ai"
    plan["workflow"] = plan.get("workflow_name") or plan.get("workflow")

    if not plan.get("tools"):
        plan["tools"] = get_tools_for_autonomous_plan(plan)
    else:
        tr = rank_tools_with_reasoning(
            plan.get("reason", ""),
            plan.get("business_intent", ""),
            limit=len(plan["tools"]),
        )
        plan["reasoning"] = tr["reasoning"]

    plan["task_type"] = f"autonomous_{_slugify_workflow(plan.get('workflow_name', 'plan'))[:40]}"
    plan["entity_type"] = (ctx.get("subject") or {}).get("type", "store").lower()
    plan["entity_id"] = (ctx.get("subject") or {}).get("id", 0)
    plan["task_payload"] = {
        "goal": plan.get("reason"),
        "business_intent": plan.get("business_intent"),
        "event": ctx["event"],
        "tools": plan.get("tools"),
        "item": ctx.get("item"),
        "store": ctx.get("store"),
        "agent": agent_context_for_plan(ctx.get("route", "hybrid"), plan.get("business_intent", "")),
    }
    plan["agent"] = plan["task_payload"]["agent"]
    plan = _attach_campaign(plan, ctx)
    return plan


def preview_plan_from_natural_language(
    text: str,
    user_id: int = 1,
) -> dict:
    """Dashboard: preview autonomous interpretation of Turkish NL rules."""
    synthetic_event = {
        "id": 0,
        "group": "intent",
        "event": "user_rule",
        "description": text,
        "payload": {"natural_language": text},
        "changes": {},
    }
    ctx = build_planner_context(
        synthetic_event,
        "intent.user_rule",
        {},
        "Store",
        1,
        user_id,
    )
    bi = bi_analyze(synthetic_event, "intent.user_rule", {}, user_id)
    ctx["business_intelligence"] = bi
    plan = _plan_from_context(ctx)
    from approval_service import assess_approval_need

    needs, risk, reason = assess_approval_need(plan)
    plan["requires_approval"] = needs or plan.get("requires_approval", False)
    plan["approval_reason"] = reason
    plan["risk_level"] = risk
    return plan


def _is_external_plan(plan: dict) -> bool:
    from approval_service import _is_external_publish_plan
    return _is_external_publish_plan(plan)


def create_plan(
    event: dict,
    event_name: str,
    context: dict,
    subject_type: str,
    subject_id: int,
    user_id: int = 1,
) -> Optional[dict]:
    if is_critical_event(event_name, event):
        return None

    ctx = build_planner_context(
        event, event_name, context, subject_type, subject_id, user_id
    )

    use_ai = os.environ.get("AUTONOMOUS_PLANNER_USE_AI", "1") == "1"

    try:
        if use_ai and os.environ.get("OPENAI_API_KEY"):
            plan = _plan_with_llm(ctx)
        else:
            plan = _plan_from_context(ctx)
    except Exception as exc:
        print(f"[AUTONOMOUS] AI planning failed: {exc}, using contextual reasoning")
        plan = _plan_from_context(ctx)

    # Validate + canonicalize + safety-score before recording.
    from plan_validator import (
        canonicalize_workflow_name,
        is_duplicate_proposal,
        safety_score,
        validate_plan,
    )
    from tool_registry import get_metadata

    available_tools = {m["name"] for m in __import__(
        "tool_registry"
    ).get_enriched_metadata()}

    ok, errors = validate_plan(plan, available_tools=available_tools)
    if not ok:
        print(f"[AUTONOMOUS] Plan invalid ({errors}); falling back to noop")
        log_ai_reasoning(
            "plan_invalid",
            f"errors={errors}",
            0.0,
            event.get("id"),
            user_id,
            {"errors": errors, "original_plan": plan},
        )
        plan = _noop(f"plan_invalid: {errors[0] if errors else 'unknown'}", 0.3)

    if plan["decision"] == "create_workflow":
        canonical = canonicalize_workflow_name(
            plan.get("business_intent", "general_marketing"),
            (ctx.get("subject") or {}).get("type", subject_type),
            (ctx.get("subject") or {}).get("id", subject_id),
            plan.get("workflow_name"),
        )
        plan["workflow_name"] = canonical
        plan["workflow"] = canonical

        if is_duplicate_proposal(
            plan,
            entity_type=plan.get("entity_type") or subject_type.lower(),
            entity_id=plan.get("entity_id") or subject_id,
            recent_memory=ctx.get("memory", {}).get("recent_decisions", []),
        ):
            print(f"[AUTONOMOUS] Duplicate proposal suppressed: {canonical}")
            plan = _noop(f"duplicate_recent_plan:{canonical}", 0.4)
        else:
            plan["safety_score"] = round(safety_score(plan, ctx), 3)
            plan["confidence"] = round(
                float(plan.get("confidence", 0.5)) * plan["safety_score"], 3
            )

    if plan["decision"] == "noop":
        record_plan(
            user_id, event, event_name, subject_type, subject_id,
            plan, "noop", reasoning_trace=plan.get("reason"),
        )
        log_autonomous_plan("noop", None, plan.get("confidence", 0), [], False, ctx.get("route", ""))
        return plan

    from approval_service import assess_approval_need

    needs, risk, appr_reason = assess_approval_need(plan)
    if needs:
        plan["requires_approval"] = True
        plan["approval_reason"] = appr_reason
        plan["risk_level"] = risk

    if plan.get("confidence", 0) < CONFIDENCE_AUTO_APPLY and _is_external_plan(plan):
        plan["requires_approval"] = True

    if not plan.get("tools"):
        plan["tools"] = get_tools_for_autonomous_plan(plan)

    record_plan(
        user_id, event, event_name, subject_type, subject_id,
        plan, "planned",
        reasoning_trace=plan.get("reasoning", plan.get("reason")),
    )

    log_autonomous_plan(
        plan["decision"],
        plan.get("workflow_name"),
        plan.get("confidence", 0),
        plan.get("tools", []),
        plan.get("requires_approval", False),
        ctx.get("route", ""),
    )
    return plan
