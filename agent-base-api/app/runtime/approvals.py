from __future__ import annotations

from datetime import datetime, timezone
import uuid


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ApprovalService:
    def __init__(self) -> None:
        self._approvals: dict[str, dict] = {}

    def create(self, workspace_id: str, task_id: str) -> dict:
        row = {
            "approval_id": f"apr_{uuid.uuid4().hex[:12]}",
            "workspace_id": workspace_id,
            "task_id": task_id,
            "status": "pending",
            "approved_by": None,
            "approved_at": None,
            "created_at": _now_iso(),
        }
        self._approvals[row["approval_id"]] = row
        return dict(row)

    def approve(self, workspace_id: str, approval_id: str, approved_by: str) -> dict | None:
        row = self._approvals.get(approval_id)
        if row is None:
            return None
        if row.get("workspace_id") != workspace_id:
            return None
        row["status"] = "approved"
        row["approved_by"] = approved_by
        row["approved_at"] = _now_iso()
        return dict(row)

    def list(self, workspace_id: str, task_id: str | None = None) -> list[dict]:
        out = [x for x in self._approvals.values() if x.get("workspace_id") == workspace_id]
        if task_id:
            out = [x for x in out if x.get("task_id") == task_id]
        return [dict(x) for x in out]
