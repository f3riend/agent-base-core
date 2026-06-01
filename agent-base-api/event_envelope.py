"""
Canonical event envelope — typed view over the fake_ai_api timeline.

Today's timeline rows are accessed as dicts with ad-hoc string indexing
(`event["group"]`, `event["subject"]["type"]`, `event.get("payload")`). That
worked for one path but it has drifted: tool-emitted events, planner-emitted
events and platform-emitted events all use slightly different shapes, and the
listener's downstream code has to handle each one defensively.

This module introduces a typed envelope. It does NOT require a schema
migration — `EventEnvelope.from_legacy` adapts existing rows, and writers
that opt-in produce envelopes via `as_row`. The listener can ingest both
shapes during the transition.

Canonical fields:
    id              — primary key
    type            — "<group>.<event>" e.g. "order.shipped"
    category        — coarse bucket: "commerce" | "marketing" | "support" | "tool"
    source          — origin tag: "fake_platform" | "tool_emit" | "planner"
    priority        — "critical" | "normal" | "background"
    tenant_id       — multi-tenant binding (was scattered as "user_id")
    causation_id    — event_id that caused this one (synthetic events)
    correlation_id  — workflow- or session-level correlation
    payload         — domain-specific payload
    meta            — orchestration metadata
    created_at      — ISO timestamp
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping


_CATEGORY_BY_GROUP_PREFIX: dict[str, str] = {
    "order": "commerce",
    "payment": "commerce",
    "stock": "commerce",
    "inventory": "commerce",
    "shipping": "commerce",
    "item": "commerce",
    "store": "commerce",
    "product": "commerce",
    "review": "support",
    "customer": "support",
    "support": "support",
    "banner": "marketing",
    "campaign": "marketing",
    "promotion": "marketing",
    "insight": "analytics",
    "sales": "analytics",
    "analytics": "analytics",
    "metric": "analytics",
    "automation": "tool",
    "tool": "tool",
    "health": "system",
    "alert": "system",
    "monitor": "system",
    "system": "system",
    "fraud": "risk",
    "risk": "risk",
}


def _category_for(group: str) -> str:
    return _CATEGORY_BY_GROUP_PREFIX.get(group, "general")


def _safe_json_load(value: Any, default: Any) -> Any:
    if not value:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


@dataclass
class EventEnvelope:
    id: int
    type: str
    category: str
    source: str
    priority: str
    tenant_id: int | None
    causation_id: int | None
    correlation_id: str
    payload: dict
    meta: dict
    created_at: str

    @property
    def group(self) -> str:
        return self.type.split(".", 1)[0] if "." in self.type else self.type

    @property
    def event(self) -> str:
        return self.type.split(".", 1)[1] if "." in self.type else ""

    @property
    def is_synthetic(self) -> bool:
        """True for events emitted by tools or planner; the listener should
        not treat these as primary triggers (they exist for BI observation)."""
        return self.source != "fake_platform"

    @property
    def subject_type(self) -> str | None:
        return (self.payload.get("subject") or {}).get("type") if isinstance(self.payload.get("subject"), dict) else None

    @property
    def subject_id(self) -> int | None:
        subj = self.payload.get("subject") if isinstance(self.payload.get("subject"), dict) else None
        if subj and subj.get("id") is not None:
            try:
                return int(subj["id"])
            except (TypeError, ValueError):
                return None
        return None

    def as_legacy_dict(self) -> dict:
        """Render in the dict shape today's listener consumes."""
        return {
            "id": self.id,
            "group": self.group,
            "event": self.event,
            "description": self.meta.get("description"),
            "payload": self.payload,
            "changes": self.meta.get("changes") or {},
            "subject": self.payload.get("subject") or {},
            "store_id": self.payload.get("store_id"),
            "ts": self.created_at,
            "meta": self.meta,
        }

    def as_row(self) -> dict:
        """Serialized form for envelope-aware writes."""
        return {
            "type": self.type,
            "category": self.category,
            "source": self.source,
            "priority": self.priority,
            "tenant_id": self.tenant_id,
            "causation_id": self.causation_id,
            "correlation_id": self.correlation_id,
            "payload": json.dumps(self.payload, default=str, ensure_ascii=False),
            "meta": json.dumps(self.meta, default=str, ensure_ascii=False),
            "created_at": self.created_at,
        }

    # ---------- Construction ----------

    @classmethod
    def from_legacy(cls, row: Mapping[str, Any]) -> "EventEnvelope":
        """Adapt an existing fake_ai_api.db timeline row.

        Existing schema columns expected on row:
            id, ts, event, log_group, description,
            subject_type, subject_id, payload, changes, meta, store_id
        """
        group = row["log_group"] if "log_group" in row.keys() else row.get("group", "unknown")
        event = row["event"] if "event" in row.keys() else "unknown"
        event_type = f"{group}.{event}"

        payload = _safe_json_load(row["payload"] if "payload" in row.keys() else row.get("payload"), {})
        changes = _safe_json_load(row["changes"] if "changes" in row.keys() else row.get("changes"), {})
        meta = _safe_json_load(row["meta"] if "meta" in row.keys() else row.get("meta"), {})

        if "description" not in meta and "description" in row.keys():
            meta["description"] = row["description"]
        if changes and "changes" not in meta:
            meta["changes"] = changes

        # Subject — payload may already carry it, otherwise derive from columns.
        if "subject" not in payload:
            st = row["subject_type"] if "subject_type" in row.keys() else None
            sid = row["subject_id"] if "subject_id" in row.keys() else None
            if st is not None or sid is not None:
                payload["subject"] = {"type": st, "id": sid}

        if "store_id" not in payload and "store_id" in row.keys():
            payload["store_id"] = row["store_id"]

        # Source detection from meta.tool_name / orchestration.path.
        if meta.get("tool_name") or meta.get("orchestration", {}).get("path") == "tool_execution":
            source = "tool_emit"
        elif meta.get("emitted_by") == "planner":
            source = "planner"
        else:
            source = meta.get("source") or "fake_platform"

        # Priority — critical events get bumped, monitoring events demoted.
        prio = meta.get("priority")
        if not prio:
            if group in ("stock", "order", "payment", "inventory", "shipping", "fraud", "risk"):
                prio = "critical"
            elif group in ("health", "alert", "monitor", "system"):
                prio = "background"
            else:
                prio = "normal"

        tenant_id = meta.get("tenant_id")
        if tenant_id is None:
            tenant_id = payload.get("tenant_id") or payload.get("user_id")

        return cls(
            id=int(row["id"]),
            type=event_type,
            category=_category_for(group),
            source=source,
            priority=prio,
            tenant_id=int(tenant_id) if tenant_id is not None else None,
            causation_id=meta.get("causation_id"),
            correlation_id=meta.get("correlation_id") or f"evt-{row['id']}",
            payload=payload,
            meta=meta,
            created_at=row["ts"] if "ts" in row.keys() else row.get("created_at", ""),
        )

    @classmethod
    def synthetic(
        cls,
        *,
        type: str,
        source: str,
        causation_id: int | None,
        payload: dict,
        meta: dict | None = None,
        tenant_id: int | None = None,
        correlation_id: str | None = None,
        priority: str = "background",
    ) -> "EventEnvelope":
        """Builder for events the runtime emits itself (tools, planner)."""
        group = type.split(".", 1)[0] if "." in type else "synthetic"
        return cls(
            id=0,
            type=type,
            category=_category_for(group),
            source=source,
            priority=priority,
            tenant_id=tenant_id,
            causation_id=causation_id,
            correlation_id=correlation_id or f"synth-{uuid.uuid4().hex[:12]}",
            payload=payload,
            meta=meta or {},
            created_at="",
        )
