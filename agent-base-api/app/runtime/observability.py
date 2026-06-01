from __future__ import annotations

from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ObservabilityService:
    def __init__(self) -> None:
        self.metrics: list[dict] = []

    def record(self, workspace_id: str, operation_id: str, metric_type: str, value: float | int, extra: dict | None = None) -> None:
        self.metrics.append(
            {
                "workspace_id": workspace_id,
                "operation_id": operation_id,
                "metric_type": metric_type,
                "value": value,
                "extra": dict(extra or {}),
                "timestamp": _now_iso(),
            }
        )
        self.metrics = self.metrics[-10000:]

    def list(self, workspace_id: str, operation_id: str | None = None) -> list[dict]:
        out = [x for x in self.metrics if x.get("workspace_id") == workspace_id]
        if operation_id:
            out = [x for x in out if x.get("operation_id") == operation_id]
        return [dict(x) for x in out]
