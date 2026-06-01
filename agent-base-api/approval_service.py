"""
Approval workflow — ONLY external social/campaign publishing requires approval.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, Optional

from db import db_connection, execute_query, now_iso
from observability import log_approval_required
from tool_registry import get_metadata


def _proposal_hash(user_id: int, proposal: dict, event_id: int | None) -> str:
    """Stable hash for dedup of approval requests for the same logical action."""
    payload = {
        "user_id": int(user_id),
        "event_id": event_id,
        "workflow_name": proposal.get("workflow_name"),
        "business_intent": proposal.get("business_intent"),
        "tools": sorted(proposal.get("tools") or []),
    }
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha1(encoded).hexdigest()

# Tools that publish externally — require human approval
EXTERNAL_PUBLISH_TOOLS = frozenset({
    "instagram_campaign_tool",
})

# Campaign workflows that imply public publishing
EXTERNAL_WORKFLOW_KEYWORDS = ("instagram", "social_publish", "public_campaign")


def init_approval_tables():
    with db_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS approval_requests (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id             INTEGER NOT NULL,
                proposal_id         INTEGER,
                event_id            INTEGER,
                workflow_name       TEXT,
                proposal_json       TEXT NOT NULL,
                status              TEXT DEFAULT 'pending',
                risk_level          TEXT DEFAULT 'medium',
                confidence          REAL,
                reason              TEXT,
                feedback            TEXT,
                edited_proposal_json TEXT,
                approved_by         TEXT,
                created_at          TEXT NOT NULL,
                updated_at          TEXT NOT NULL
            )
        """)


def _is_external_publish_plan(plan: dict) -> bool:
    """True only for social/campaign public publishing."""
    tools = set(plan.get("tools") or [])
    if tools & EXTERNAL_PUBLISH_TOOLS:
        return True
    wf = (plan.get("workflow_name") or "").lower()
    if any(k in wf for k in EXTERNAL_WORKFLOW_KEYWORDS):
        return True
    intent = (plan.get("business_intent") or "").lower()
    if intent in ("instagram_content", "social_publish", "public_campaign"):
        return True
    # Dinamik LangGraph approval_gate node'u proposal.approval_type'i set ediyor.
    # Bu post_approval/story_approval/banner_approval/campaign_approval'sa dış
    # yayın anlamına gelir — assess_approval_need'i tetikle.
    atype = (plan.get("approval_type") or "").strip().lower()
    if atype in ("post_approval", "story_approval", "banner_approval", "campaign_approval"):
        return True
    return False


def assess_approval_need(plan: dict) -> tuple[bool, str, str]:
    """
    Returns (requires_approval, risk_level, reason).
    Internal analysis, FAQ, insights — NO approval.
    """
    if not _is_external_publish_plan(plan):
        return False, "low", "Dahili işlem — onay gerekmez"

    for tool_name in plan.get("tools", []):
        if tool_name in EXTERNAL_PUBLISH_TOOLS:
            return (
                True,
                "medium",
                f"Dış sosyal yayın: {tool_name} — insan onayı gerekli",
            )

    return True, "medium", "Kampanya/sosyal medya dış yayını — onay gerekli"


def create_approval_request(
    user_id: int,
    proposal: dict,
    proposal_id: int | None = None,
    event_id: int | None = None,
    approval_type: str = "generic_approval",
) -> int:
    init_approval_tables()
    requires, risk, reason = assess_approval_need(proposal)
    if not requires:
        return 0

    # approval_type'i proposal'dan da alabilir (geriye dönük uyum)
    if not approval_type or approval_type == "generic_approval":
        prop_type = (proposal or {}).get("approval_type")
        if isinstance(prop_type, str) and prop_type.strip():
            approval_type = prop_type.strip()

    phash = _proposal_hash(user_id, proposal, event_id)

    # Cheap pre-check before relying on the partial unique index.
    existing = execute_query(
        """
        SELECT id FROM approval_requests
        WHERE user_id=? AND proposal_hash=? AND status='pending'
        ORDER BY id DESC LIMIT 1
        """,
        (user_id, phash),
        one=True,
    )
    if existing:
        print(f"[APPROVAL] Dedup hit: pending request #{existing['id']}")
        return int(existing["id"])

    ts = now_iso()
    try:
        with db_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO approval_requests (
                    user_id, proposal_id, event_id, workflow_name,
                    proposal_json, proposal_hash, status, risk_level,
                    confidence, reason, approval_type, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    proposal_id,
                    event_id,
                    proposal.get("workflow_name"),
                    json.dumps(proposal),
                    phash,
                    risk,
                    float(proposal.get("confidence", 0)),
                    reason,
                    approval_type,
                    ts,
                    ts,
                ),
            )
            req_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        existing = execute_query(
            """
            SELECT id FROM approval_requests
            WHERE user_id=? AND proposal_hash=? AND status='pending'
            ORDER BY id DESC LIMIT 1
            """,
            (user_id, phash),
            one=True,
        )
        if existing:
            print(f"[APPROVAL] Race deduped → existing #{existing['id']}")
            return int(existing["id"])
        raise

    log_approval_required(
        req_id,
        reason,
        float(proposal.get("confidence", 0)),
        risk,
    )
    return req_id


def get_pending_approvals(
    user_id: int,
    limit: int = 50,
    *,
    approval_type: str | None = None,
) -> list[dict]:
    init_approval_tables()
    sql = "SELECT * FROM approval_requests WHERE user_id=? AND status='pending'"
    params: list = [user_id]
    if approval_type:
        sql += " AND approval_type=?"
        params.append(approval_type)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    rows = execute_query(sql, tuple(params))
    result = []
    for r in rows:
        d = dict(r)
        d["proposal"] = json.loads(d.pop("proposal_json", "{}") or "{}")
        if d.get("edited_proposal_json"):
            d["edited_proposal"] = json.loads(d["edited_proposal_json"])
        result.append(d)
    return result


def get_approval_type_summary(user_id: int | None = None) -> list[dict]:
    """Mevcut approval_type'lara göre toplam ve pending sayıları döner.

    UI dinamik sekme oluşturmak için kullanır.
    """
    init_approval_tables()
    if user_id is not None:
        rows = execute_query(
            """
            SELECT
                COALESCE(approval_type, 'generic_approval') AS approval_type,
                COUNT(*)                                     AS total,
                SUM(CASE WHEN status='pending'  THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN status='approved' THEN 1 ELSE 0 END) AS approved,
                SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END) AS rejected
            FROM approval_requests
            WHERE user_id=?
            GROUP BY COALESCE(approval_type, 'generic_approval')
            ORDER BY pending DESC, total DESC
            """,
            (int(user_id),),
        )
    else:
        rows = execute_query(
            """
            SELECT
                COALESCE(approval_type, 'generic_approval') AS approval_type,
                COUNT(*)                                     AS total,
                SUM(CASE WHEN status='pending'  THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN status='approved' THEN 1 ELSE 0 END) AS approved,
                SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END) AS rejected
            FROM approval_requests
            GROUP BY COALESCE(approval_type, 'generic_approval')
            ORDER BY pending DESC, total DESC
            """,
        )
    return [dict(r) for r in rows]


def get_approval(approval_id: int) -> dict | None:
    row = execute_query(
        "SELECT * FROM approval_requests WHERE id=?",
        (approval_id,),
        one=True,
    )
    if not row:
        return None
    d = dict(row)
    d["proposal"] = json.loads(d.get("proposal_json") or "{}")
    return d


def approve(approval_id: int, approved_by: str = "dashboard_user") -> dict:
    req = get_approval(approval_id)
    if not req:
        return {"success": False, "error": "not_found"}
    if req["status"] != "pending":
        return {"success": False, "error": f"invalid_status:{req['status']}"}

    proposal = json.loads(req.get("edited_proposal_json") or req["proposal_json"])
    proposal["requires_approval"] = False
    proposal["approved"] = True

    ts = now_iso()
    with db_connection() as conn:
        conn.execute(
            """
            UPDATE approval_requests
            SET status='approved', approved_by=?, updated_at=?
            WHERE id=?
            """,
            (approved_by, ts, approval_id),
        )

    from planner_memory import record_feedback, record_planner_outcome
    from planner_learning import record_outcome

    record_feedback(req["user_id"], approval_id, "approved", proposal=proposal)
    record_outcome(req["user_id"], "approval:approved", True)
    record_planner_outcome(
        user_id=req["user_id"],
        memory_id=None,
        workflow_id=None,
        outcome="approved",
        workflow_name=proposal.get("workflow_name"),
        business_intent=proposal.get("business_intent"),
    )
    return {"success": True, "proposal": proposal, "approval_id": approval_id}


def reject(
    approval_id: int,
    feedback: str = "",
    rejected_by: str = "dashboard_user",
) -> dict:
    req = get_approval(approval_id)
    if not req:
        return {"success": False, "error": "not_found"}

    ts = now_iso()
    with db_connection() as conn:
        conn.execute(
            """
            UPDATE approval_requests
            SET status='rejected', feedback=?, approved_by=?, updated_at=?
            WHERE id=?
            """,
            (feedback, rejected_by, ts, approval_id),
        )

    from planner_memory import record_feedback, record_planner_outcome
    from planner_learning import record_outcome

    proposal = json.loads(req.get("proposal_json") or "{}")
    record_feedback(req["user_id"], approval_id, "rejected", feedback=feedback)
    record_outcome(req["user_id"], "approval:rejected", False)
    record_planner_outcome(
        user_id=req["user_id"],
        memory_id=None,
        workflow_id=None,
        outcome="rejected",
        workflow_name=proposal.get("workflow_name"),
        business_intent=proposal.get("business_intent"),
        feedback=feedback,
    )
    return {"success": True, "approval_id": approval_id}


def edit_proposal(approval_id: int, edited_proposal: dict) -> dict:
    req = get_approval(approval_id)
    if not req:
        return {"success": False, "error": "not_found"}

    ts = now_iso()
    with db_connection() as conn:
        conn.execute(
            """
            UPDATE approval_requests
            SET edited_proposal_json=?, updated_at=?
            WHERE id=?
            """,
            (json.dumps(edited_proposal), ts, approval_id),
        )
    return {"success": True, "approval_id": approval_id}


def retry_approval(approval_id: int) -> dict:
    req = get_approval(approval_id)
    if not req:
        return {"success": False, "error": "not_found"}

    ts = now_iso()
    with db_connection() as conn:
        conn.execute(
            """
            UPDATE approval_requests
            SET status='pending', feedback=NULL, updated_at=?
            WHERE id=?
            """,
            (ts, approval_id),
        )
    return {"success": True, "approval_id": approval_id}


def submit_feedback(approval_id: int, feedback: str) -> dict:
    ts = now_iso()
    with db_connection() as conn:
        conn.execute(
            """
            UPDATE approval_requests
            SET feedback=?, updated_at=?
            WHERE id=?
            """,
            (feedback, ts, approval_id),
        )
    from planner_memory import record_feedback

    row = get_approval(approval_id)
    if row:
        record_feedback(row["user_id"], approval_id, "feedback", feedback=feedback)
    return {"success": True}


def tool_requires_approval(tool_name: str) -> bool:
    return tool_name in EXTERNAL_PUBLISH_TOOLS
