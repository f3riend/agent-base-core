"""
AI Operator chat — multi-turn cognition over real retrieved data.

Pipeline per turn:

    open or load session
       │
       ▼
    resolve follow-up references         (conversation_memory.resolve_follow_up)
       │                                  e.g. "peki neden?" → "Satışlar neden bu durumda?"
       ▼
    route resolved question → retrieval  (business_query_router → business_retrieval_service)
       │                                  fact-grounded data
       ▼
    open-ended fallback if no route      (narrative_synth deterministic narrative)
       │
       ▼
    synthesize natural Turkish prose     (ai_synthesizer — LLM-backed; fallback to baseline)
       │
       ▼
    record turn                          (conversation_memory.record_turn)
       │                                  → updates anti-phrase list + active entity

The chat output now includes a `stages` array (deliberation lines for the
UI) and a `mode` field ("llm" | "deterministic_fallback") so the dashboard
can show what shape the answer took.

This module never queries the database directly. It composes the
retrieval + memory + synthesis layers.
"""

from __future__ import annotations

import json
from typing import Any, Optional

import ai_synthesizer
import business_query_router as query_router
import conversation_memory as memory
from business_intelligence import analyze
from business_state import build_business_state
from context_compressor import compress_timeline
from cross_event_reasoner import reason_across_events
from db import execute_query
from narrative_synth import synthesize_narrative
from planner_learning import get_learning_summary
from planner_memory import get_memory_summary_for_api
from timeline_service import fetch_timeline


# Hints that explicitly want a broad summary instead of a specific answer.
_OPEN_ENDED_HINTS = (
    "öneri", "tavsiye", "strateji",
    "genel durum", "özet", "ne durumda", "summary", "overview",
    "neler oluyor",
)


def _is_open_ended(question: str) -> bool:
    q = (question or "").lower()
    return any(h in q for h in _OPEN_ENDED_HINTS)


def _fetch_workflows(user_id: int, limit: int = 15) -> list[dict]:
    rows = execute_query(
        """
        SELECT workflow_name, status, created_at, entity_type, entity_id
        FROM workflow_instances WHERE user_id=? ORDER BY id DESC LIMIT ?
        """,
        (user_id, limit),
    )
    return [dict(r) for r in rows]


def _entity_from_retrieval(retrieval: dict) -> tuple[str | None, int | None, str | None]:
    """Extract (type, id, human_label) from a retrieval payload, if present."""
    data = (retrieval or {}).get("data") or {}
    item = data.get("item")
    if isinstance(item, dict) and item.get("id"):
        return "item", int(item["id"]), item.get("name")
    items = data.get("items")
    if isinstance(items, list) and items:
        first = items[0]
        if isinstance(first, dict) and first.get("id"):
            return "item", int(first["id"]), first.get("name")
    return None, None, None


def _build_open_ended_retrieval(question: str, user_id: int) -> dict:
    """When the router returns None, fall back to a state-snapshot 'retrieval'
    shape so the synthesizer still gets a single, structured fact bundle.
    """
    state = build_business_state(user_id)
    timeline_data = fetch_timeline(limit=30, direction="desc")
    events = timeline_data.get("data", [])
    cross = reason_across_events(user_id)
    workflows = _fetch_workflows(user_id)

    synthetic_event = {
        "id": 0, "group": "chat", "event": "query",
        "description": question,
        "payload": {"natural_language": question},
        "changes": {},
    }
    bi = analyze(synthetic_event, "chat.query", {}, user_id)

    out = synthesize_narrative(
        intent="recommendations" if _is_open_ended(question) else "general",
        state=state,
        bi=bi,
        cross=cross,
        memory={},
        workflows=workflows,
    )

    return {
        "intent": "open_ended",
        "routed_intent": "open_ended",
        "answer": out.get("narrative") or (
            "Şu an net konuşabileceğim bir sinyal görmüyorum. "
            "Daha spesifik bir soru sorarsan veriye doğrudan bakabilirim."
        ),
        "data": {
            "state_summary": {
                "inventory_health": (state.get("inventory") or {}).get("health"),
                "active_workflows": (state.get("campaigns") or {}).get("active_count", 0),
                "negative_reviews": (state.get("engagement") or {}).get("negative_reviews", 0),
                "sentiment": state.get("sentiment"),
            },
            "cross_event_summary": cross.get("summary"),
            "primary_hypothesis": cross.get("primary_hypothesis"),
            "bi_insights": [
                ins.get("message", str(ins))
                for ins in (bi.get("insights") or [])[:5]
            ],
        },
        "recommendations": out.get("recommendations", []),
        "confidence": out.get("confidence", 0.55),
        "timeline_compressed": compress_timeline(events, 8),
    }


def answer_question(
    question: str,
    user_id: int = 1,
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Answer a chat question with real retrieved data + conversation memory.

    Args:
        question:    raw user input.
        user_id:     tenant binding.
        session_id:  client-supplied session token (dashboard localStorage).
                     If omitted, a new session is opened and returned to the
                     caller for follow-up turns.

    Returns: see body — the canonical chat response shape.
    """
    question = (question or "").strip()
    if not question:
        return {
            "question": "",
            "intent": "empty",
            "answer": "Sormak istediğin bir konu yazabilirsin — satış, stok, "
                      "kampanya, müşteri yorumları, iş akışları, kargo...",
            "data": {},
            "recommendations": [],
            "confidence": 0.0,
            "sources": [],
            "stages": [],
            "session_id": session_id,
            "mode": "noop",
            "is_followup": False,
        }

    # ----- Open / load session -----
    session = memory.open_session(user_id=user_id, session_id=session_id)
    sid = session["id"]

    # ----- Tur 3: Conversational rule edit early-dispatch -----
    # Operatör "kuralı kapat", "delay'i 5 güne çıkar" gibi konuşuyorsa
    # retrieval + synthesizer bypass — doğrudan rule_edit servisi yanıtı
    # ile cevap ver.
    try:
        import conversational_rule_edit as _cre
        active_rule_id, active_rule_name = memory.get_active_rule(sid)
        edit_intent = _cre.detect_edit_intent(
            question,
            user_id=user_id,
            session_active_rule_id=active_rule_id,
        )
    except Exception as exc:
        print(f"[CHAT] edit detect skipped: {exc}")
        edit_intent = None

    if edit_intent is not None and edit_intent.confidence >= 0.65:
        # Edit niyeti algılandı — silme ise confirm gerek
        confirm = (edit_intent.kind == "delete"
                   and "evet" in question.lower()
                   and "sil" in question.lower())
        result = _cre.apply_edit(
            edit_intent, user_id=user_id, confirm_delete=confirm,
        )

        # Session active rule güncelle
        if result.rule_id:
            memory.set_active_rule(sid, result.rule_id, None)

        # Konuşma turunu kaydet
        memory.record_turn(
            session_id=sid, user_id=user_id,
            question=question, resolved_question=None,
            intent="conversational_rule_edit",
            routed_intent=f"edit:{edit_intent.kind}",
            primary_entity_type="rule",
            primary_entity_id=result.rule_id,
            primary_entity_label=edit_intent.target_rule_label,
            answer=result.summary,
            confidence=edit_intent.confidence,
        )
        return {
            "question": question,
            "resolved_question": None,
            "is_followup": False,
            "follow_up_rationale": "",
            "session_id": sid,
            "intent": "conversational_rule_edit",
            "routed_intent": f"edit:{edit_intent.kind}",
            "active_entity": edit_intent.target_rule_label,
            "active_rule_id": result.rule_id,
            "answer": result.summary,
            "stages": [
                f"Niyet: kural #{edit_intent.target_rule_id or '?'} → "
                f"{edit_intent.kind}",
                "Değişikliği uyguladım." if result.success else "İşlem yapılmadı.",
            ],
            "data": {
                "edit_intent": edit_intent.to_dict(),
                "edit_result": result.to_dict(),
            },
            "recommendations": [],
            "confidence": edit_intent.confidence,
            "mode": "rule_edit",
            "model": None,
            "latency_ms": 0,
            "sources": ["conversational_rule_edit"],
            "fallback": False,
            "anti_repetition_active": False,
        }

    # ----- Resolve follow-up reference -----
    resolution = memory.resolve_follow_up(question, sid)
    resolved_q = resolution.resolved_question

    # ----- Retrieval -----
    retrieval = query_router.route(resolved_q, user_id=user_id)
    used_fallback = retrieval is None
    if used_fallback:
        retrieval = _build_open_ended_retrieval(resolved_q, user_id)

    # ----- Memory context for the LLM -----
    mem_ctx = memory.conversation_context(sid)

    # ----- Synthesize natural prose -----
    synth = ai_synthesizer.synthesize(
        question=question,
        resolved_question=resolved_q,
        retrieval=retrieval,
        memory_ctx=mem_ctx,
        is_followup=resolution.is_followup,
        inherited_label=resolution.inherited_entity_label,
    )

    answer_text = synth.answer

    # ----- Record turn -----
    entity_type, entity_id, entity_label = _entity_from_retrieval(retrieval)
    # If retrieval didn't bring its own entity but we inherited one, keep that.
    if not entity_label and resolution.inherited_entity_label:
        entity_label = resolution.inherited_entity_label

    routed_intent = retrieval.get("routed_intent") or retrieval.get("intent")

    memory.record_turn(
        session_id=sid,
        user_id=user_id,
        question=question,
        resolved_question=resolved_q if resolution.is_followup else None,
        intent=routed_intent if not used_fallback else "open_ended",
        routed_intent=routed_intent,
        primary_entity_type=entity_type,
        primary_entity_id=entity_id,
        primary_entity_label=entity_label,
        answer=answer_text,
        confidence=retrieval.get("confidence"),
    )

    sources: list[str] = ["retrieval"] if not used_fallback else [
        "business_state", "business_intelligence", "cross_event_reasoning",
    ]
    if synth.mode == "llm":
        sources.append("ai_synthesizer")
    else:
        sources.append("deterministic_fallback")

    return {
        "question": question,
        "resolved_question": resolved_q if resolution.is_followup else None,
        "is_followup": resolution.is_followup,
        "follow_up_rationale": resolution.rationale,
        "session_id": sid,
        "intent": routed_intent if not used_fallback else "open_ended",
        "routed_intent": retrieval.get("routed_intent"),
        "active_entity": entity_label,
        "answer": answer_text,
        "stages": synth.stages,
        "data": retrieval.get("data", {}),
        "recommendations": retrieval.get("recommendations", []),
        "confidence": retrieval.get("confidence", 0.6),
        "mode": synth.mode,
        "model": synth.model,
        "latency_ms": synth.latency_ms,
        "sources": sources,
        "fallback": used_fallback,
        "anti_repetition_active": bool(mem_ctx.get("anti_phrases")),
    }


def supported_query_intents() -> list[str]:
    """Exposed to /api/internal/chat/intents for the dashboard help tooltip."""
    return query_router.list_supported_intents()
