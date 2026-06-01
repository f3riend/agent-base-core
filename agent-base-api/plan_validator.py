"""
Plan validator + canonicalization + duplicate suppression.

Three responsibilities:
    1. Robust parsing of model output (no more `re.search(r'\\{[\\s\\S]*\\}', ...)`).
    2. Schema + ontology validation of planner output.
    3. Canonical workflow naming so semantically identical plans collapse.

This module is the gate between "the planner produced something" and "the
runtime trusts it enough to schedule a workflow".
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from ontology import (
    INTENTS,
    default_tools_for_intent,
    domain_for_intent,
    is_known_intent,
)


# ---------------------------------------------------------------------------
# Plan schema
# ---------------------------------------------------------------------------


class PlannerOutput(BaseModel):
    decision: Literal["create_workflow", "cancel_workflow", "noop"]
    workflow_name: str | None = None
    reason: str
    tools: list[str] = Field(default_factory=list)
    priority: Literal["low", "medium", "high"] = "medium"
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    requires_approval: bool = False
    business_intent: str = "general_marketing"
    delay: int = Field(ge=0, le=30, default=0)

    @field_validator("workflow_name")
    @classmethod
    def _wf_name_format(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        if len(value) > 64:
            value = value[:64]
        if not re.fullmatch(r"[a-z0-9_]{3,64}", value):
            raise ValueError(
                "workflow_name must be snake_case [a-z0-9_], length 3..64"
            )
        return value


# ---------------------------------------------------------------------------
# Robust JSON parsing (replaces AR-5 regex)
# ---------------------------------------------------------------------------


def _strip_markdown_fences(text: str) -> str:
    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL)
    return fence.group(1) if fence else text


def _balanced_object(text: str) -> str | None:
    """Find the first balanced {...} block. Tolerates strings & escapes."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def parse_planner_output(raw: str) -> dict:
    """Best-effort JSON object extraction. Returns {} on total failure.

    Order of attempts:
        1. json.loads on the raw text
        2. json.loads on the markdown-fence-stripped text
        3. json.loads on the first balanced {...} block
    """
    if not raw:
        return {}
    for candidate in (raw, _strip_markdown_fences(raw), _balanced_object(raw) or ""):
        candidate = candidate.strip()
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return {}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_plan(plan: dict, available_tools: set[str] | None = None) -> tuple[bool, list[str]]:
    """Returns (ok, errors). Strict — invalid plans should fall back to noop."""
    errors: list[str] = []
    if not isinstance(plan, dict):
        return False, ["plan is not a dict"]

    try:
        parsed = PlannerOutput(**{
            k: plan.get(k) for k in PlannerOutput.model_fields if k in plan
        })
    except ValidationError as exc:
        for err in exc.errors():
            loc = ".".join(str(x) for x in err.get("loc", []))
            errors.append(f"{loc}: {err.get('msg')}")
        return False, errors

    intent = parsed.business_intent
    if intent and not is_known_intent(intent) and intent != "general_marketing":
        errors.append(f"unknown business_intent: {intent}")

    if available_tools is not None:
        unknown = [t for t in parsed.tools if t not in available_tools]
        if unknown:
            errors.append(f"unknown tools: {unknown}")

    if parsed.decision == "create_workflow" and not parsed.workflow_name:
        errors.append("create_workflow requires workflow_name")

    return (not errors), errors


# ---------------------------------------------------------------------------
# Canonicalization
# ---------------------------------------------------------------------------


def canonicalize_workflow_name(
    intent: str,
    entity_type: str,
    entity_id: int | None,
    base_name: str | None = None,
) -> str:
    """Deterministic workflow naming.

    Identical (intent, entity_type, entity_id) tuples produce the same name,
    so duplicate suppression in the workflow service catches them. The base
    name (from the planner) is folded in as a short suffix for human
    readability but the prefix is canonical.
    """
    safe_intent = re.sub(r"[^a-z0-9_]", "_", (intent or "general").lower())[:24].strip("_")
    safe_entity = re.sub(r"[^a-z0-9]", "", (entity_type or "store").lower())[:8] or "store"
    eid = int(entity_id) if isinstance(entity_id, int) else 0

    name = f"{safe_intent}_{safe_entity}_{eid}"

    if base_name:
        suffix = re.sub(r"[^a-z0-9_]", "_", base_name.lower())[:16].strip("_")
        if suffix and suffix not in name:
            name = f"{name}_{suffix}"

    return name[:60].rstrip("_") or "autonomous_workflow"


# ---------------------------------------------------------------------------
# Duplicate suppression
# ---------------------------------------------------------------------------


def plan_signature(plan: dict, entity_type: str, entity_id: int | None) -> str:
    """Stable hash for "same logical plan" detection."""
    payload = {
        "intent": plan.get("business_intent"),
        "workflow_name": plan.get("workflow_name"),
        "tools": sorted(plan.get("tools") or []),
        "entity_type": entity_type,
        "entity_id": entity_id,
    }
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha1(encoded).hexdigest()[:16]


def is_duplicate_proposal(
    plan: dict,
    entity_type: str,
    entity_id: int | None,
    recent_memory: list[dict],
    *,
    window_seconds: int = 600,
) -> bool:
    """True if an identical plan was recorded in the last `window_seconds`.

    `recent_memory` is the planner_memory rows for this user/entity. Each row
    should expose at minimum `created_at`, `workflow_name`, `tools_json`,
    `plan_json`.
    """
    if not recent_memory:
        return False

    target_sig = plan_signature(plan, entity_type, entity_id)
    now_ts = time.time()
    for row in recent_memory:
        try:
            sig_payload = {
                "intent": (json.loads(row.get("plan_json") or "{}")).get("business_intent"),
                "workflow_name": row.get("workflow_name"),
                "tools": sorted(json.loads(row.get("tools_json") or "[]")),
                "entity_type": entity_type,
                "entity_id": entity_id,
            }
            sig = hashlib.sha1(
                json.dumps(sig_payload, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()[:16]
        except (json.JSONDecodeError, TypeError):
            continue

        if sig != target_sig:
            continue

        created = row.get("created_at")
        if not created:
            return True
        try:
            from datetime import datetime
            age = now_ts - datetime.fromisoformat(created).timestamp()
            if age < window_seconds:
                return True
        except ValueError:
            return True
    return False


# ---------------------------------------------------------------------------
# Safety score
# ---------------------------------------------------------------------------


def safety_score(plan: dict, ctx: dict) -> float:
    """0..1 multiplier on confidence.

    Heuristics:
        - external publish tools without explicit approval → score down
        - unknown intent → score down
        - matched memory of recent failures for this intent → score down
        - matched memory of recent successes → score up
    """
    score = 1.0

    intent = plan.get("business_intent") or ""
    if not is_known_intent(intent):
        score -= 0.2

    tools = set(plan.get("tools") or [])
    if "instagram_campaign_tool" in tools and not plan.get("requires_approval"):
        score -= 0.15

    mem = ctx.get("memory") or {}
    if mem.get("failed_count", 0) > 2:
        score -= 0.1
    if mem.get("successful_campaigns"):
        score += 0.05

    return max(0.1, min(1.0, score))
