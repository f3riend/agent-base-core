"""
LangGraph execution state — typed, reducible, checkpoint-friendly.

State, graph boyunca her node'un okuyup yazabileceği shared dict. LangGraph
TypedDict + Annotated reducers ile çalışır: bir node bir alanı return
ettiğinde reducer onu mevcut state'le birleştirir.

Tasarım kararları:
    - Her major alt-domain (event, content, risk, approval, publish, monitor)
      bir Pydantic model. Bu yapı, prompt'larda strict JSON kullanılırken
      hata yakalamayı kolaylaştırır.
    - trace_events bir liste; reducer ile append-only akıyor. Bu sayede
      hangi node'da ne olduğu LangGraph checkpoint'inin dışında da
      sorgulanabiliyor.
    - status alanları operatör UI'ı için string. "running" / "ok" / "failed"
      / "waiting_human" gibi anlamlı değerler.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, TypedDict

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Sub-domain Pydantic models
# ---------------------------------------------------------------------------


class EventContext(BaseModel):
    """Tetikleyen olay + ilgili entity bilgisi."""
    model_config = ConfigDict(extra="allow")

    event_id: int
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    subject_type: str | None = None
    subject_id: int | None = None
    store: dict[str, Any] | None = None
    item: dict[str, Any] | None = None
    received_at: str | None = None


class GeneratedContent(BaseModel):
    """İçerik üretici node'unun çıktısı."""
    model_config = ConfigDict(extra="allow")

    channel: str = "instagram"
    template: str = "generic"
    headline: str = ""
    body: str = ""
    caption: str = ""
    hashtags: list[str] = Field(default_factory=list)
    image_prompt: str | None = None
    image_url: str | None = None
    extras: dict[str, Any] = Field(default_factory=dict)


class RiskAssessment(BaseModel):
    """Risk analiz node'unun çıktısı."""
    model_config = ConfigDict(extra="allow")

    risk_level: str = "low"          # low | medium | high
    risk_score: float = Field(default=0.0, ge=0.0, le=1.0)
    flags: list[str] = Field(default_factory=list)
    requires_human: bool = False
    explanation: str = ""


class ApprovalDecision(BaseModel):
    """Onay node'unun durumu — interrupt sonrası resume ile doldurulur."""
    model_config = ConfigDict(extra="allow")

    approval_id: int | None = None
    decision: str = "pending"        # pending | approved | rejected | edited
    decided_by: str | None = None
    feedback: str | None = None
    edited_content: dict[str, Any] | None = None


class PublishResult(BaseModel):
    """Yayın node'unun çıktısı."""
    model_config = ConfigDict(extra="allow")

    channel: str = "instagram"
    mode: str = "draft_only"         # draft_only | real_publish_would_happen | real
    account_handle: str | None = None
    credential_id: int | None = None
    timeline_event_id: int | None = None
    success: bool = True
    message: str = ""


class MonitorResult(BaseModel):
    """İzleme node'unun çıktısı."""
    model_config = ConfigDict(extra="allow")

    scheduled_check_at: str | None = None
    initial_metrics: dict[str, Any] = Field(default_factory=dict)
    note: str = ""


class TraceEvent(BaseModel):
    """Bir node'un başlama/bitiş/hata olayı — UI timeline'ı için."""
    model_config = ConfigDict(extra="allow")

    node: str
    status: str                      # started | ok | failed | interrupted
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)
    ts: str
    duration_ms: int | None = None


# ---------------------------------------------------------------------------
# Reducers — birleştirme kuralları
# ---------------------------------------------------------------------------


def _append_traces(left: list, right: list) -> list:
    if not left:
        return list(right or [])
    if not right:
        return list(left)
    return list(left) + list(right)


def _merge_dict(left: dict, right: dict) -> dict:
    """Sığ-merge: aynı anahtar için right'taki değer kazanır."""
    if not left:
        return dict(right or {})
    if not right:
        return dict(left)
    out = dict(left)
    out.update(right)
    return out


def _take_last(left: Any, right: Any) -> Any:
    """Her güncellemede sadece en sonki değer geçerli."""
    return right if right is not None else left


# ---------------------------------------------------------------------------
# Top-level state TypedDict (LangGraph'ın bekledigi form)
# ---------------------------------------------------------------------------


class RuleExecutionState(TypedDict, total=False):
    """LangGraph state — TypedDict + Annotated reducers."""

    # Yürütme tanımlayıcıları
    execution_id: Annotated[int | None, _take_last]
    rule_id: Annotated[int, _take_last]
    user_id: Annotated[int, _take_last]
    org_id: Annotated[int | None, _take_last]
    thread_id: Annotated[str, _take_last]

    # Rule snapshot (StructuredRule.to_storage_dict())
    rule: Annotated[dict[str, Any], _take_last]

    # Tetik bağlamı
    event: Annotated[dict[str, Any], _take_last]  # EventContext.model_dump()

    # Domain alt-state'leri (her node ilgilisini doldurur)
    content: Annotated[dict[str, Any] | None, _take_last]
    risk: Annotated[dict[str, Any] | None, _take_last]
    approval: Annotated[dict[str, Any] | None, _take_last]
    publish: Annotated[dict[str, Any] | None, _take_last]
    monitor: Annotated[dict[str, Any] | None, _take_last]

    # Akış-kontrol bayrakları
    status: Annotated[str, _take_last]          # running | waiting_human | completed | failed | cancelled
    current_node: Annotated[str | None, _take_last]
    last_error: Annotated[str | None, _take_last]

    # Observability
    trace_events: Annotated[list[dict[str, Any]], _append_traces]
    metadata: Annotated[dict[str, Any], _merge_dict]


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def initial_state(
    *,
    rule_id: int,
    user_id: int,
    org_id: int | None,
    thread_id: str,
    rule_dict: dict[str, Any],
    event_dict: dict[str, Any],
) -> RuleExecutionState:
    """Graph başlatmadan önce çağrılır — minimal güvenli zemin döndürür."""
    return {
        "execution_id": None,
        "rule_id": rule_id,
        "user_id": user_id,
        "org_id": org_id,
        "thread_id": thread_id,
        "rule": rule_dict,
        "event": event_dict,
        "content": None,
        "risk": None,
        "approval": None,
        "publish": None,
        "monitor": None,
        "status": "running",
        "current_node": None,
        "last_error": None,
        "trace_events": [],
        "metadata": {},
    }


def make_trace(
    node: str,
    status: str,
    summary: str,
    *,
    details: dict[str, Any] | None = None,
    duration_ms: int | None = None,
) -> dict[str, Any]:
    return {
        "node": node,
        "status": status,
        "summary": summary,
        "details": details or {},
        "ts": datetime.utcnow().isoformat(),
        "duration_ms": duration_ms,
    }
