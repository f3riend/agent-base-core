"""
AI Planner Fallback — proposes actions only, never executes tools or workflows.

When no deterministic rule matches, the planner may suggest:
  { "decision": "create_workflow" | "cancel_workflow" | "noop", ... }

Runtime (planner_runtime.py) decides whether to apply via workflow engine.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

KNOWN_WORKFLOWS = {
    "welcome_instagram_post",
    "low_stock_alert",
}

ENTITY_EVENT_HINTS = {
    "store.created": ("store", "welcome_instagram_post"),
    "store.rejected": ("store", "welcome_instagram_post"),
    "stock.updated": ("item", "low_stock_alert"),
    "product.created": ("item", None),
}


def _noop(reason: str, confidence: float = 1.0) -> dict:
    return {
        "decision": "noop",
        "workflow": None,
        "delay": 0,
        "reason": reason,
        "confidence": confidence,
        "source": "heuristic",
    }


def propose_action_heuristic(
    event_name: str,
    context: dict,
    subject_type: str,
    subject_id: int,
) -> dict:
    """Offline-capable planner — pattern-based proposals only."""

    if event_name == "store.created":
        store = context.get("store", {})
        if store.get("status") in ("rejected", "deleted", "cancelled"):
            return _noop("store not eligible for welcome campaign")

        return {
            "decision": "create_workflow",
            "workflow": "welcome_instagram_post",
            "delay": 0,
            "reason": (
                "No rule matched store.created — planner proposes "
                "welcome instagram launch workflow"
            ),
            "confidence": 0.75,
            "source": "heuristic",
        }

    if event_name == "store.rejected":
        return {
            "decision": "cancel_workflow",
            "workflow": "welcome_instagram_post",
            "delay": 0,
            "reason": (
                "No rule matched store.rejected — planner proposes "
                "cancelling welcome campaign"
            ),
            "confidence": 0.85,
            "source": "heuristic",
        }

    if event_name == "stock.updated":
        item = context.get("item", {})
        stock = item.get("stock")

        if stock is not None and stock < 10:
            return {
                "decision": "create_workflow",
                "workflow": "low_stock_alert",
                "delay": 0,
                "reason": (
                    f"No rule matched stock.updated — planner proposes "
                    f"low_stock_alert (stock={stock})"
                ),
                "confidence": 0.8,
                "source": "heuristic",
            }

        return _noop(f"stock level {stock} does not require alert")

    return _noop(f"no planner policy for event {event_name}")


def propose_action_ai(
    event_name: str,
    context: dict,
    subject_type: str,
    subject_id: int,
) -> dict:
    """Legacy fallback planner — native OpenAI (CrewAI kaldırıldı, Tur 2).

    Sadece CRITICAL_FALLBACK=1 ortamında çağırılır; default kapalı.
    """
    from openai import OpenAI

    client = OpenAI(timeout=12)
    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": (
                "You are an orchestration planner. Propose automation "
                "decisions for unmatched events. You MUST NOT execute tools "
                "or workflows. Return JSON only.\n"
                "Allowed decisions: create_workflow, cancel_workflow, noop.\n"
                "Allowed workflows: welcome_instagram_post, low_stock_alert.\n"
                "Use 'noop' if uncertain."
            )},
            {"role": "user", "content": (
                f"An event arrived with NO matching deterministic rule.\n\n"
                f"Event: {event_name}\nSubject: {subject_type} #{subject_id}\n"
                f"Context: {json.dumps(context, default=str)[:6000]}\n\n"
                'Return ONLY JSON:\n'
                '{\n'
                '  "decision": "create_workflow" | "cancel_workflow" | "noop",\n'
                '  "workflow": "welcome_instagram_post" | "low_stock_alert" | null,\n'
                '  "delay": 0,\n'
                '  "reason": "explanation",\n'
                '  "confidence": 0.0 to 1.0\n'
                '}'
            )},
        ],
        temperature=0.2,
        max_tokens=400,
    )
    result = (completion.choices[0].message.content or "").strip()

    match = re.search(r"\{[\s\S]*\}", result)
    if not match:
        raise ValueError("AI planner did not return JSON object")

    proposal = json.loads(match.group())
    proposal["source"] = "ai"
    return _normalize_proposal(proposal)


def _normalize_proposal(proposal: dict) -> dict:
    decision = proposal.get("decision", "noop")

    if decision not in ("create_workflow", "cancel_workflow", "noop"):
        decision = "noop"

    workflow = proposal.get("workflow")
    if workflow and workflow not in KNOWN_WORKFLOWS:
        return _noop(f"unknown workflow rejected: {workflow}")

    if decision == "create_workflow" and not workflow:
        return _noop("create_workflow requires workflow name")

    if decision == "cancel_workflow" and not workflow:
        return _noop("cancel_workflow requires workflow name")

    return {
        "decision": decision,
        "workflow": workflow,
        "delay": int(proposal.get("delay", 0)),
        "reason": proposal.get("reason", "ai planner proposal"),
        "confidence": float(proposal.get("confidence", 0.5)),
        "source": proposal.get("source", "ai"),
    }


def propose_action(
    event_name: str,
    context: dict,
    subject_type: str,
    subject_id: int,
    event: dict | None = None,
) -> dict | None:
    """
    Propose an orchestration decision when no rule matched.
    Returns None if planner is disabled.
    """
    enabled = os.environ.get("AI_PLANNER_ENABLED", "0") == "1"
    if not enabled:
        return None

    min_confidence = float(os.environ.get("AI_PLANNER_MIN_CONFIDENCE", "0.6"))
    use_ai = os.environ.get("AI_PLANNER_USE_AI", "0") == "1"

    try:
        if use_ai and os.environ.get("OPENAI_API_KEY"):
            proposal = propose_action_ai(
                event_name, context, subject_type, subject_id
            )
        else:
            proposal = propose_action_heuristic(
                event_name, context, subject_type, subject_id
            )
    except Exception as exc:
        print(f"[PLANNER] AI proposal failed: {exc}, using heuristic")
        proposal = propose_action_heuristic(
            event_name, context, subject_type, subject_id
        )

    if proposal["decision"] == "noop":
        print(f"[PLANNER] noop: {proposal['reason']}")
        return proposal

    if proposal.get("confidence", 0) < min_confidence:
        print(
            f"[PLANNER] below confidence threshold "
            f"({proposal['confidence']} < {min_confidence})"
        )
        return _noop("confidence below threshold")

    print(
        f"[PLANNER] proposal: {proposal['decision']} "
        f"workflow={proposal.get('workflow')} "
        f"reason={proposal['reason']}"
    )
    return proposal
