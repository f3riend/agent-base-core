from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from pydantic import BaseModel, Field

from app.api.auth import get_current_user
from app.models.user import User


router = APIRouter(prefix="/client-debug", tags=["client-debug"])
client_debug_logger = logger.bind(module="client-debug")


class BrowserLogEntry(BaseModel):
    ts: str = ""
    event: str = Field(default="", max_length=160)
    payload: Any = None


class BrowserLogBatch(BaseModel):
    page: str = Field(default="", max_length=512)
    debugEnabled: bool = False
    entries: list[BrowserLogEntry] = Field(default_factory=list)


def _compact_payload(payload: Any) -> str:
    try:
        raw = json.dumps(payload, ensure_ascii=False, default=str)
    except TypeError:
        raw = str(payload)
    return raw if len(raw) <= 2000 else raw[:2000] + "...[truncated]"


@router.post("/browser-logs")
def ingest_browser_logs(body: BrowserLogBatch, user: User = Depends(get_current_user)) -> dict[str, int]:
    entries = body.entries[:100]
    if not entries:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Log kaydi yok.")

    page = body.page or "-"
    for entry in entries:
        event = entry.event or "unknown"
        client_debug_logger.info(
            "browser_log user_id={} workspace={} page={} debug={} ts={} event={} payload={}",
            user.id,
            user.workspace_uid,
            page,
            body.debugEnabled,
            entry.ts or "-",
            event,
            _compact_payload(entry.payload),
        )
    return {"accepted": len(entries)}
