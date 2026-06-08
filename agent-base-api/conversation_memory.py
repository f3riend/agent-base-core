"""
Conversation memory — PostgreSQL backed, OpenAI-native chat history.

Üç katmanlı hafıza:
  - SHORT-TERM: build_openai_messages → son N turu role=user/assistant
    olarak OpenAI'a verir. Native follow-up resolution (manuel rewrite yok).
  - MEDIUM-TERM: session özetleri (chat_sessions.summary), gpt-4o-mini ile.
  - LONG-TERM: user_memory tablosu (per-user kalıcı notlar).

PG tabloları (migration 016):
  - bchat_sessions(id TEXT PK, user_id, opened_at, last_turn_at, summary, meta JSONB)
  - bchat_turns(id, session_id, question, answer, intent, model_used, tokens_used, cost_usd, created_at)
  - user_memory(id, user_id, memory_key, memory_value, source, updated_at)

set_active_rule / get_active_rule — orchestration_api'nin rule-edit yolu için
bchat_sessions.meta JSONB içinde tutulur (eski SQLite alanları kalktı).
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _session():
    from app.core.database import SessionLocal
    return SessionLocal()


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------


def open_session(user_id: int, session_id: str | None = None) -> dict:
    """Idempotent: returns the session row, creating it if needed."""
    sid = session_id or f"sess_{uuid.uuid4().hex[:16]}"
    try:
        with _session() as s:
            row = s.execute(
                text(
                    "SELECT id, user_id, opened_at, last_turn_at, summary, meta "
                    "FROM bchat_sessions WHERE id = :sid AND user_id = :uid"
                ),
                {"sid": sid, "uid": int(user_id)},
            ).first()
            if row:
                return _row_to_session(row)

            s.execute(
                text(
                    "INSERT INTO bchat_sessions (id, user_id, meta) "
                    "VALUES (:sid, :uid, '{}'::jsonb)"
                ),
                {"sid": sid, "uid": int(user_id)},
            )
            s.commit()
            return {
                "id": sid,
                "user_id": int(user_id),
                "opened_at": None,
                "last_turn_at": None,
                "summary": None,
                "meta": {},
            }
    except Exception as exc:
        print(f"[CONV_MEMORY] open_session failed: {exc}")
        return {
            "id": sid, "user_id": int(user_id),
            "opened_at": None, "last_turn_at": None,
            "summary": None, "meta": {},
        }


def get_session(session_id: str) -> dict | None:
    if not session_id:
        return None
    try:
        with _session() as s:
            row = s.execute(
                text(
                    "SELECT id, user_id, opened_at, last_turn_at, summary, meta "
                    "FROM bchat_sessions WHERE id = :sid"
                ),
                {"sid": session_id},
            ).first()
            return _row_to_session(row) if row else None
    except Exception as exc:
        print(f"[CONV_MEMORY] get_session failed: {exc}")
        return None


def _row_to_session(row) -> dict:
    meta = row.meta if isinstance(row.meta, dict) else (json.loads(row.meta) if row.meta else {})
    return {
        "id": row.id,
        "user_id": row.user_id,
        "opened_at": row.opened_at,
        "last_turn_at": row.last_turn_at,
        "summary": row.summary,
        "meta": meta or {},
    }


# ---------------------------------------------------------------------------
# Turn recording
# ---------------------------------------------------------------------------


def record_turn(
    *,
    session_id: str,
    user_id: int,
    question: str,
    answer: str,
    intent: str | None = None,
    model_used: str | None = None,
    tokens_used: int | None = None,
    cost_usd: float | None = None,
    **_legacy,
) -> int | None:
    """Persist a turn. Returns turn id or None on failure."""
    if not session_id:
        return None
    try:
        with _session() as s:
            row = s.execute(
                text(
                    "INSERT INTO bchat_turns ("
                    " session_id, user_id, question, answer, intent, "
                    " model_used, tokens_used, cost_usd"
                    ") VALUES ("
                    " :sid, :uid, :q, :a, :intent, :model, :tokens, :cost"
                    ") RETURNING id"
                ),
                {
                    "sid": session_id, "uid": int(user_id),
                    "q": question, "a": answer,
                    "intent": intent, "model": model_used,
                    "tokens": tokens_used, "cost": cost_usd,
                },
            ).first()
            s.execute(
                text(
                    "UPDATE bchat_sessions SET last_turn_at = NOW() WHERE id = :sid"
                ),
                {"sid": session_id},
            )
            s.commit()
            return int(row.id) if row else None
    except Exception as exc:
        print(f"[CONV_MEMORY] record_turn failed: {exc}")
        return None


def recent_turns(session_id: str, limit: int = 10) -> list[dict]:
    if not session_id:
        return []
    try:
        with _session() as s:
            rows = s.execute(
                text(
                    "SELECT id, question, answer, intent, model_used, "
                    "tokens_used, cost_usd, created_at "
                    "FROM bchat_turns WHERE session_id = :sid "
                    "ORDER BY id DESC LIMIT :lim"
                ),
                {"sid": session_id, "lim": int(limit)},
            ).all()
            out = [
                {
                    "id": r.id,
                    "question": r.question,
                    "answer": r.answer,
                    "intent": r.intent,
                    "model_used": r.model_used,
                    "tokens_used": r.tokens_used,
                    "cost_usd": float(r.cost_usd) if r.cost_usd is not None else None,
                    "created_at": r.created_at,
                }
                for r in rows
            ]
            return list(reversed(out))
    except Exception as exc:
        print(f"[CONV_MEMORY] recent_turns failed: {exc}")
        return []


# ---------------------------------------------------------------------------
# SHORT-TERM: OpenAI native chat history
# ---------------------------------------------------------------------------


def build_openai_messages(
    *,
    session_id: str,
    system_prompt: str,
    new_question: str,
    context_data: str = "",
    limit_turns: int = 10,
) -> list[dict]:
    """OpenAI Chat Completions için messages listesi oluştur.

    Yapı:
      [
        {role: system, content: system_prompt},
        {role: user, content: önceki soru},
        {role: assistant, content: önceki cevap},
        ...
        {role: user, content: yeni soru + context_data},
      ]

    Native chat history sayesinde "peki ya diğeri?", "az önce ne dedin?" gibi
    follow-up'lar LLM tarafından çözülür — manuel rewrite gerekmez.
    """
    messages: list[dict] = [{"role": "system", "content": system_prompt}]

    history = recent_turns(session_id, limit=limit_turns)
    for turn in history:
        q = (turn.get("question") or "").strip()
        a = (turn.get("answer") or "").strip()
        if q:
            messages.append({"role": "user", "content": q})
        if a:
            messages.append({"role": "assistant", "content": a})

    user_content = (new_question or "").strip()
    if context_data:
        user_content = f"{user_content}\n\n--- VERİ ---\n{context_data}"
    messages.append({"role": "user", "content": user_content})

    return messages


# ---------------------------------------------------------------------------
# MEDIUM-TERM: Session summaries
# ---------------------------------------------------------------------------


_SUMMARIZE_PROMPT = (
    "Aşağıda bir kullanıcı ile e-ticaret asistanı arasındaki konuşma var. "
    "3 cümlede özetle: kullanıcı ne sordu, ne öğrendi, ne karar verdi? "
    "Sadece düz Türkçe metin yaz, başlık veya madde işareti kullanma."
)


def summarize_session(session_id: str, api_key: str | None = None) -> str | None:
    """Sessionu özetle ve bchat_sessions.summary'ye yaz."""
    if not session_id:
        return None
    key = api_key or os.environ.get("OPENAI_API_KEY")
    if not key:
        return None

    turns = recent_turns(session_id, limit=30)
    if not turns:
        return None

    convo_lines = []
    for t in turns:
        q = (t.get("question") or "").strip()
        a = (t.get("answer") or "").strip()
        if q:
            convo_lines.append(f"K: {q[:200]}")
        if a:
            convo_lines.append(f"A: {a[:200]}")
    convo = "\n".join(convo_lines)

    try:
        from openai import OpenAI

        client = OpenAI(api_key=key, timeout=10)
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _SUMMARIZE_PROMPT},
                {"role": "user", "content": convo},
            ],
            temperature=0.3,
            max_tokens=200,
        )
        summary = (completion.choices[0].message.content or "").strip()
        if summary:
            with _session() as s:
                s.execute(
                    text("UPDATE bchat_sessions SET summary = :sm WHERE id = :sid"),
                    {"sm": summary, "sid": session_id},
                )
                s.commit()
        return summary
    except Exception as exc:
        print(f"[CONV_MEMORY] summarize_session failed: {exc}")
        return None


def get_session_summary(user_id: int, limit: int = 3) -> str:
    """Kullanıcının son N session özetini al, system prompt'a eklenecek metin döner."""
    try:
        with _session() as s:
            rows = s.execute(
                text(
                    "SELECT summary FROM bchat_sessions "
                    "WHERE user_id = :uid AND summary IS NOT NULL "
                    "ORDER BY last_turn_at DESC NULLS LAST LIMIT :lim"
                ),
                {"uid": int(user_id), "lim": int(limit)},
            ).all()
            summaries = [r.summary for r in rows if r.summary]
            if not summaries:
                return ""
            return "\n".join(f"- {sm}" for sm in summaries)
    except Exception as exc:
        print(f"[CONV_MEMORY] get_session_summary failed: {exc}")
        return ""


# ---------------------------------------------------------------------------
# LONG-TERM: User memory
# ---------------------------------------------------------------------------


_EXTRACT_PROMPT = (
    "Aşağıda bir e-ticaret asistanı ile satıcı arasındaki bir konuşma turu var. "
    "Satıcı hakkında uzun vadede hatırlanacak NET bir bilgi varsa JSON döndür: "
    '{"key": "kısa_snake_case", "value": "kısa açıklama"}. '
    "Hatırlanacak bir şey yoksa SADECE 'null' yaz, başka hiçbir şey yazma. "
    "Örnek key'ler: razer_strategy, indirim_tercihi, oncelikli_urun, hedef_marj, "
    "raporlama_tercihi. Geçici / tek seferlik bilgileri kaydetme."
)


def extract_and_save_memories(
    session_id: str,
    user_id: int,
    question: str,
    answer: str,
    api_key: str | None = None,
) -> dict | None:
    """Konuşmadan kalıcı hatırlanması gereken bilgi çıkar, user_memory'ye yaz."""
    key_env = api_key or os.environ.get("OPENAI_API_KEY")
    if not key_env or not (question and answer):
        return None
    try:
        from openai import OpenAI

        client = OpenAI(api_key=key_env, timeout=8)
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _EXTRACT_PROMPT},
                {"role": "user", "content": f"Soru: {question}\nCevap: {answer}"},
            ],
            temperature=0,
            max_tokens=100,
        )
        raw = (completion.choices[0].message.content or "").strip()
        if not raw or raw.lower() == "null":
            return None
        # JSON'u temizle
        if raw.startswith("```"):
            raw = raw.strip("`").lstrip("json").strip()
        data = json.loads(raw)
        mkey = (data.get("key") or "").strip()[:64]
        mval = (data.get("value") or "").strip()[:500]
        if not mkey or not mval:
            return None
        _upsert_memory(user_id, mkey, mval)
        return {"key": mkey, "value": mval}
    except Exception as exc:
        print(f"[CONV_MEMORY] extract_and_save_memories failed: {exc}")
        return None


def _upsert_memory(user_id: int, key: str, value: str, source: str = "auto") -> None:
    with _session() as s:
        s.execute(
            text(
                "INSERT INTO user_memory (user_id, memory_key, memory_value, source) "
                "VALUES (:uid, :k, :v, :src) "
                "ON CONFLICT (user_id, memory_key) DO UPDATE "
                "SET memory_value = EXCLUDED.memory_value, "
                "    updated_at = NOW(), source = EXCLUDED.source"
            ),
            {"uid": int(user_id), "k": key, "v": value, "src": source},
        )
        s.commit()


def get_user_memories(user_id: int, limit: int = 30) -> str:
    """Kullanıcının tüm uzun vadeli notlarını system prompt'a eklenecek metin döner."""
    try:
        with _session() as s:
            rows = s.execute(
                text(
                    "SELECT memory_key, memory_value FROM user_memory "
                    "WHERE user_id = :uid ORDER BY updated_at DESC LIMIT :lim"
                ),
                {"uid": int(user_id), "lim": int(limit)},
            ).all()
            if not rows:
                return ""
            return "\n".join(f"- {r.memory_key}: {r.memory_value}" for r in rows)
    except Exception as exc:
        print(f"[CONV_MEMORY] get_user_memories failed: {exc}")
        return ""


# ---------------------------------------------------------------------------
# Active rule — meta JSONB'de tutulur (orchestration_api uyumluluğu)
# ---------------------------------------------------------------------------


def set_active_rule(
    session_id: str,
    rule_id: int | None,
    rule_name: str | None = None,
) -> None:
    if not session_id:
        return
    try:
        with _session() as s:
            s.execute(
                text(
                    "UPDATE bchat_sessions "
                    "SET meta = COALESCE(meta, '{}'::jsonb) "
                    "|| jsonb_build_object('active_rule_id', :rid, 'active_rule_name', :rn) "
                    "WHERE id = :sid"
                ),
                {
                    "rid": int(rule_id) if rule_id else None,
                    "rn": rule_name,
                    "sid": session_id,
                },
            )
            s.commit()
    except Exception as exc:
        print(f"[CONV_MEMORY] set_active_rule failed: {exc}")


def get_active_rule(session_id: str) -> tuple[int | None, str | None]:
    if not session_id:
        return None, None
    try:
        with _session() as s:
            row = s.execute(
                text(
                    "SELECT meta->>'active_rule_id' AS rid, "
                    "       meta->>'active_rule_name' AS rn "
                    "FROM bchat_sessions WHERE id = :sid"
                ),
                {"sid": session_id},
            ).first()
            if not row:
                return None, None
            rid = int(row.rid) if row.rid and row.rid.isdigit() else None
            return rid, row.rn
    except Exception as exc:
        print(f"[CONV_MEMORY] get_active_rule failed: {exc}")
        return None, None


# ---------------------------------------------------------------------------
# Legacy compatibility shims — eski business_chat akışı kalan kısımlardan
# çağrı yapabilir. Yeni akış native chat history kullanır, bunlar boş döner.
# ---------------------------------------------------------------------------


@dataclass
class FollowUpResolution:
    is_followup: bool
    resolved_question: str
    inherited_entity_label: str | None
    inherited_intent: str | None
    rationale: str


def resolve_follow_up(question: str, session_id: str) -> FollowUpResolution:
    """LEGACY: yeni akışta native chat history kullanılıyor, bu no-op'a indi."""
    return FollowUpResolution(
        is_followup=False,
        resolved_question=question or "",
        inherited_entity_label=None,
        inherited_intent=None,
        rationale="native_history",
    )


def conversation_context(session_id: str, limit_turns: int = 4) -> dict:
    """LEGACY shim — ai_synthesizer.compose_prompt eski yola düşerse kullanır."""
    return {
        "session_id": session_id,
        "active_entity_label": None,
        "active_entity_type": None,
        "active_intent": None,
        "history": [],
        "anti_phrases": [],
    }


def anti_phrase_list(session_id: str, limit_turns: int = 4) -> list[str]:
    return []


def init_conversation_tables() -> None:
    """LEGACY no-op — tablolar artık alembic 016 ile gelir."""
    return None
