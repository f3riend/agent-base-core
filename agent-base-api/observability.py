"""
Structured observability for AI-native orchestration runtime.

Every emit produces a tagged stdout line. Emits with persist=True also write
to listener.db.orchestration_traces so the dashboard can render the AI
reasoning feed without parsing stdout. Persistence is best-effort: a DB
failure logs to stderr and does not break the calling code path.

Trace summaries are humanized BEFORE persistence so the dashboard's
"AI Düşünce Akışı" panel reads as operator language, not technical strings.
The raw `details_json` keeps everything for debugging.
"""

from __future__ import annotations

import json
import time
import traceback
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Humanization layer for the reasoning feed
# ---------------------------------------------------------------------------


# Map raw technical phrases → operator-language equivalents. Applied at
# write time on the `summary` field. The full structured payload is kept
# untouched in `details_json` for debugging and DB queries.
_PHRASE_REWRITES: tuple[tuple[str, str], ...] = (
    ("stable_operations",           "operasyon stabil görünüyor"),
    ("promotion_working",           "promosyon etkili çalışıyor"),
    ("customer_dissatisfaction",    "müşteri memnuniyetsizliği sinyali"),
    ("delivery_experience_issue",   "teslimat deneyiminde sorun"),
    ("campaign_momentum",           "kampanya momentumu yakaladı"),
    ("supply_chain_pressure",       "tedarik tarafında baskı"),
    ("growth_window",               "büyüme penceresi açık"),
    ("reputation_strength",         "marka itibarı güçlü"),
    ("plan_invalid",                "AI bu planı güvenlik nedeniyle reddetti"),
    ("plan_synthesis",              "AI plan sentezi tamamlandı"),
    ("synthetic_skip",              "yardımcı olaylar atlandı"),
    ("processed_by_rule_engine",    "kural motoru tarafından işlendi"),
    ("workflow_created",            "iş akışı oluşturuldu"),
    ("workflow_cancelled",          "iş akışı iptal edildi"),
    ("workflow_name",               "iş akışı adı"),
    ("circuit_open",                "araç devresi açıldı (geçici durdurma)"),
    ("approval required",           "onay gerekli"),
    ("approval_required",           "onay gerekli"),
    ("selected ",                   "şu araçları seçti: "),
    (" tool(s)",                    ""),
    ("ROUTING",                     "yönlendirme"),
)


_TAG_HUMAN: dict[str, str] = {
    "AI_REASONING":            "AI değerlendirme",
    "AUTONOMOUS_PLAN":         "otonom plan",
    "BUSINESS_SIGNAL":         "iş sinyali",
    "CROSS_EVENT_REASONING":   "çapraz analiz",
    "TOOL_SELECTION":          "araç seçimi",
    "TOOL_SANDBOX":            "araç çalıştırma",
    "TOOL_SANDBOX_TIMEOUT":    "araç zaman aşımı",
    "TOOL_CIRCUIT_OPEN":       "araç devresi açıldı",
    "APPROVAL_REQUIRED":       "onay isteği",
    "ROUTING":                 "olay yönlendirme",
    "WORKFLOW_LATENCY":        "iş akışı süresi",
    "WORKFLOW_TRACE":          "iş akışı izi",
    "MEMORY_SUMMARY":          "hafıza özeti",
    "SCHEDULE_CREATED":        "yeni planlama",
    "SCHEDULE_FIRED":          "planlanan iş tetiklendi",
    "SCHEDULE_MOVED":          "planlama ertelendi",
    "SCHEDULE_CANCELLED":      "planlama iptal edildi",
    "SCHEDULE_FAILED":         "planlama başarısız",
    "CUSTOMER_THREAD_OPENED":  "müşteri konuşması başladı",
    "CUSTOMER_DRAFT_CREATED":  "müşteri yanıt taslağı",
    "CUSTOMER_THREAD_ESCALATED": "müşteri konuşması eskale edildi",
    "CUSTOMER_THREAD_RESOLVED":  "müşteri konuşması kapandı",
}


def _humanize_summary(tag: str, raw: str | None) -> str:
    """Convert technical summary phrases to operator language."""
    if not raw:
        return _TAG_HUMAN.get(tag, "")
    text = str(raw)
    for needle, replacement in _PHRASE_REWRITES:
        if needle in text:
            text = text.replace(needle, replacement)
    return text[:400]


def _emit(
    tag: str,
    payload: dict,
    *,
    persist: bool = False,
    user_id: int | None = None,
    event_id: int | None = None,
    workflow_id: int | None = None,
    task_id: int | None = None,
):
    """Stdout always; orchestration_traces table when persist=True."""
    print(f"[{tag}] {json.dumps(payload, default=str, ensure_ascii=False)}")

    if not persist:
        return

    try:
        # Imported lazily so this module stays importable in environments
        # where the orchestration DB hasn't been initialised yet.
        from db import db_connection, now_iso

        summary_raw = (
            payload.get("summary")
            or payload.get("reason")
            or payload.get("message")
            or ""
        )
        if isinstance(summary_raw, (dict, list)):
            summary_raw = json.dumps(summary_raw, default=str, ensure_ascii=False)
        summary = _humanize_summary(tag, summary_raw)

        with db_connection() as conn:
            conn.execute(
                """
                INSERT INTO orchestration_traces (
                    user_id, event_id, workflow_id, task_id,
                    trace_tag, summary, details_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id or 1,
                    event_id,
                    workflow_id,
                    task_id,
                    tag,
                    summary,
                    json.dumps(payload, default=str, ensure_ascii=False),
                    now_iso(),
                ),
            )
    except Exception as exc:
        # Never fail the calling code path on a trace persistence error.
        print(f"[OBSERVABILITY] trace persist failed: {exc}")
        traceback.print_exc()


def log_ai_reasoning(
    stage: str,
    reason: str,
    confidence: float | None = None,
    event_id: int | None = None,
    user_id: int | None = None,
    extra: dict | None = None,
):
    _emit(
        "AI_REASONING",
        {
            "stage": stage,
            "reason": reason,
            "confidence": confidence,
            "event_id": event_id,
            "user_id": user_id,
            **(extra or {}),
        },
        persist=True,
        user_id=user_id,
        event_id=event_id,
    )


def log_tool_selection(
    tools: list[str],
    scores: dict[str, float] | None = None,
    reason: str = "",
    event_id: int | None = None,
    user_id: int | None = None,
):
    _emit(
        "TOOL_SELECTION",
        {
            "tools": tools,
            "scores": scores or {},
            "reason": reason,
            "event_id": event_id,
            "summary": f"selected {len(tools)} tool(s): {', '.join(tools[:3])}",
        },
        persist=True,
        user_id=user_id,
        event_id=event_id,
    )


def log_business_signal(
    signal: str,
    strength: float,
    source: str,
    details: dict | None = None,
    user_id: int | None = None,
    event_id: int | None = None,
):
    _emit(
        "BUSINESS_SIGNAL",
        {
            "signal": signal,
            "strength": round(strength, 3),
            "source": source,
            "details": details or {},
            "summary": f"{signal} strength={strength:.2f} ({source})",
        },
        persist=True,
        user_id=user_id,
        event_id=event_id,
    )


def log_approval_required(
    proposal_id: int | None,
    reason: str,
    confidence: float,
    risk_level: str = "medium",
    user_id: int | None = None,
):
    _emit(
        "APPROVAL_REQUIRED",
        {
            "proposal_id": proposal_id,
            "reason": reason,
            "confidence": confidence,
            "risk_level": risk_level,
            "summary": f"approval required ({risk_level}): {reason}",
        },
        persist=True,
        user_id=user_id,
    )


def log_autonomous_plan(
    decision: str,
    workflow_name: str | None,
    confidence: float,
    tools: list[str],
    requires_approval: bool,
    route: str = "",
    user_id: int | None = None,
    event_id: int | None = None,
):
    _emit(
        "AUTONOMOUS_PLAN",
        {
            "decision": decision,
            "workflow_name": workflow_name,
            "confidence": confidence,
            "tools": tools,
            "requires_approval": requires_approval,
            "route": route,
            "summary": f"{decision} {workflow_name or '-'} confidence={confidence:.2f}",
        },
        persist=True,
        user_id=user_id,
        event_id=event_id,
    )


def log_workflow_latency(
    workflow_id: int,
    workflow_name: str,
    latency_ms: int,
    status: str,
    user_id: int | None = None,
):
    _emit(
        "WORKFLOW_LATENCY",
        {
            "workflow_id": workflow_id,
            "workflow_name": workflow_name,
            "latency_ms": latency_ms,
            "status": status,
            "summary": f"{workflow_name} {status} {latency_ms}ms",
        },
        persist=True,
        user_id=user_id,
        workflow_id=workflow_id,
    )


def log_cross_event_reasoning(
    hypothesis: str,
    summary: str,
    confidence: float,
    signals: dict | None = None,
    user_id: int | None = None,
):
    _emit(
        "CROSS_EVENT_REASONING",
        {
            "hypothesis": hypothesis,
            "summary": summary,
            "confidence": confidence,
            "signals": signals or {},
        },
        persist=True,
        user_id=user_id,
    )


def log_memory_summary(
    summary: str,
    record_count: int,
    user_id: int | None = None,
):
    _emit(
        "MEMORY_SUMMARY",
        {
            "summary": summary[:500],
            "record_count": record_count,
            "user_id": user_id,
        },
        persist=True,
        user_id=user_id,
    )


def log_routing_decision(
    event_name: str,
    route: str,
    confidence: float | None = None,
    reason: str = "",
    user_id: int | None = None,
    event_id: int | None = None,
):
    _emit(
        "ROUTING",
        {
            "event": event_name,
            "route": route,
            "confidence": confidence,
            "reason": reason,
            "summary": f"{event_name} → {route} ({confidence})",
        },
        persist=True,
        user_id=user_id,
        event_id=event_id,
    )


def log_workflow_trace(
    workflow_id: int,
    workflow_name: str,
    status: str,
    reason: str = "",
    user_id: int | None = None,
):
    _emit(
        "WORKFLOW_TRACE",
        {
            "workflow_id": workflow_id,
            "workflow_name": workflow_name,
            "status": status,
            "reason": reason,
            "summary": f"workflow #{workflow_id} {workflow_name} → {status}",
        },
        persist=True,
        user_id=user_id,
        workflow_id=workflow_id,
    )


# ---------------------------------------------------------------------------
# Read API — dashboard reasoning feed
# ---------------------------------------------------------------------------


def fetch_traces(
    user_id: int | None = None,
    trace_tag: str | None = None,
    event_id: int | None = None,
    limit: int = 50,
) -> list[dict]:
    from db import execute_query

    sql = "SELECT * FROM orchestration_traces WHERE 1=1"
    params: list = []

    if user_id is not None:
        sql += " AND user_id=?"
        params.append(user_id)
    if trace_tag:
        sql += " AND trace_tag=?"
        params.append(trace_tag)
    if event_id is not None:
        sql += " AND event_id=?"
        params.append(event_id)

    sql += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit))

    rows = execute_query(sql, tuple(params))
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["details"] = json.loads(d.pop("details_json", "{}") or "{}")
        except json.JSONDecodeError:
            d["details"] = {}
        out.append(d)
    return out


def fetch_trace_tags() -> list[dict]:
    from db import execute_query

    rows = execute_query(
        """
        SELECT trace_tag, COUNT(*) as cnt
        FROM orchestration_traces
        GROUP BY trace_tag
        ORDER BY cnt DESC
        """
    )
    return [{"tag": r["trace_tag"], "count": r["cnt"]} for r in rows]


class MetricTimer:
    def __init__(self, label: str):
        self.label = label
        self._start = time.monotonic()

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self._start) * 1000)

    def finish(self, **extra):
        ms = self.elapsed_ms()
        _emit("METRIC", {"label": self.label, "ms": ms, **extra})
        return ms
