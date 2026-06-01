from __future__ import annotations

from enum import StrEnum


class OperationState(StrEnum):
    created = "created"
    queued = "queued"
    running = "running"
    waiting_approval = "waiting_approval"
    scheduled = "scheduled"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


TRANSITION_MAP: dict[OperationState, set[OperationState]] = {
    OperationState.created: {OperationState.queued, OperationState.cancelled},
    OperationState.queued: {OperationState.running, OperationState.cancelled, OperationState.failed},
    OperationState.running: {
        OperationState.waiting_approval,
        OperationState.scheduled,
        OperationState.completed,
        OperationState.failed,
        OperationState.cancelled,
    },
    OperationState.waiting_approval: {
        OperationState.running,
        OperationState.scheduled,
        OperationState.completed,
        OperationState.failed,
        OperationState.cancelled,
    },
    OperationState.scheduled: {OperationState.completed, OperationState.failed, OperationState.cancelled},
    OperationState.completed: set(),
    OperationState.failed: set(),
    OperationState.cancelled: set(),
}


def guard_transition(current: str, target: str) -> None:
    cur = OperationState(current)
    tgt = OperationState(target)
    allowed = TRANSITION_MAP.get(cur, set())
    if tgt not in allowed:
        raise ValueError(f"Invalid state transition: {cur.value} -> {tgt.value}")
