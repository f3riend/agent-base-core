"""
Business query router — nl_to_sql tabanlı dinamik sorgu sistemi.

Akış: mention → rate-limit → access → cache → nl_to_sql → cache yaz → döndür
Güvenlik: nl_to_sql sadece SELECT geçirir, UUID cast garantili.
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Optional


_PRODUCT_PRONOUN_RE = re.compile(r"\bbu\s+ürün(\w*)\b", flags=re.IGNORECASE)


def _rewrite_pronouns(question: str, entity_label: str) -> tuple[str, bool]:
    """'bu ürün*' kalıplarını entity_label ile ikame eder.

    Suffix korunur — 'bu ürünün' → '"Anker Soundcore" ürününün'. Tek satır rewrite
    Türkçe gramerinin tamamını çözmez ama nl_to_sql şablon eşleştirmesi için
    yeterli; LLM kalan suffix'leri context'ten anlar.
    """
    if not entity_label or not question:
        return question, False
    safe_label = entity_label.replace('"', "'")
    new_q, n = _PRODUCT_PRONOUN_RE.subn(
        lambda m: f'"{safe_label}" ürün{m.group(1)}',
        question,
    )
    return new_q, n > 0


def route(
    question: str,
    *,
    user_id: int = 1,
    active_entity_label: str = "",
    active_entity_id: str | None = None,
    active_entity_type: str | None = None,
    session_id: str | None = None,
    **extra,
) -> Optional[dict]:
    question = (question or "").strip()
    if not question:
        return None

    from app.services.mention_parser import parse_mention
    from app.services.access_control import is_admin_user, resolve_scope_to_store_ids
    from app.services.chat_cache import get_cached, set_cache
    from app.services.rate_limiter import check_rate_limit
    from app.services.nl_to_sql import nl_to_sql

    is_admin = is_admin_user(user_id)
    api_key = os.environ.get("OPENAI_API_KEY")

    # 1) Rate limit
    allowed, _remaining = check_rate_limit(user_id, is_admin)
    if not allowed:
        return {
            "intent": "rate_limited",
            "routed_intent": "rate_limited",
            "answer": "Çok fazla soru gönderiyorsun, biraz bekle.",
            "data": {"pg_context": {}, "op_context": {}, "rate_limited": True},
            "recommendations": [],
            "confidence": 1.0,
        }

    # 2) @mention parse
    mention = parse_mention(question)
    clean_q = mention.clean_query or question

    # 2b) Pronoun rewriting — "bu ürün" referansı varsa active_entity ile ikame.
    # Rewrite yapıldıysa "bu turun aktif entity'si" tartışmasız önceki prev_active.
    # business_chat ikinci bir lookup yapmasın diye resolved_entity_* alanları
    # data payload'una atomik şekilde konur — desync engellenir.
    rewritten_q, was_rewritten = _rewrite_pronouns(clean_q, active_entity_label or "")
    resolved_entity_label: str | None = None
    resolved_entity_id: str | None = None
    resolved_entity_type: str | None = None
    if was_rewritten:
        print(
            f"[ROUTER] pronoun rewrite: {clean_q!r} → {rewritten_q!r} "
            f"(active_entity={active_entity_label!r})"
        )
        clean_q = rewritten_q
        resolved_entity_label = active_entity_label
        resolved_entity_id = active_entity_id
        resolved_entity_type = active_entity_type or "product"

    # 3) Erişim kontrolü
    store_ids = resolve_scope_to_store_ids(mention, user_id, is_admin)

    # 4) Cache kontrolü — soru + store_ids birlikte hash
    scope_hash = hashlib.md5(
        f"{'-'.join(sorted(store_ids))}:{clean_q}".encode()
    ).hexdigest()[:12]

    cached = get_cached(user_id, "nl_query", scope_hash)
    if cached:
        return {
            "intent": "nl_query",
            "routed_intent": "smart_query",
            "answer": "",
            "data": {
                "pg_context": {
                    "type": "smart_context",
                    "text": cached,
                    "row_count": 1,  # cache'deyse daha önce row_count>0'dı
                    "description": "cache",
                },
                "op_context": {},
                "from_cache": True,
                "model_override": "gpt-4o-mini",
            },
            "recommendations": [],
            "confidence": 0.95,
        }

    # 5) NL → SQL → Çalıştır
    result = nl_to_sql(
        question=clean_q,
        store_ids=store_ids,
        user_id=user_id,
        api_key=api_key,
        is_admin=is_admin,
    )

    formatted = result.get("formatted") or ""
    row_count = result.get("row_count", 0)
    model_tier = result.get("model_tier") or "mini"
    model = "gpt-4o" if (model_tier == "full" or is_admin) else "gpt-4o-mini"

    # 6) Cache'e yaz — sadece veri geldiyse
    if formatted and not result.get("error") and row_count > 0:
        set_cache(user_id, "nl_query", scope_hash, formatted, row_count=row_count)

    return {
        "intent": "nl_query",
        "routed_intent": "smart_query",
        "answer": "",
        "data": {
            "pg_context": {
                "type": "smart_context",
                "text": formatted,
                "sql": result.get("sql", ""),
                "description": result.get("description", ""),
                "row_count": row_count,
                "error": result.get("error"),
                "is_error": bool(result.get("is_error")),
            },
            "op_context": {},
            "model_override": model,
            "from_cache": False,
            "mention_scope": mention.scope,
            "store_ids": store_ids,
            "effective_question": clean_q,
            "pronoun_rewritten": was_rewritten,
            "active_entity_label": active_entity_label or None,
            "resolved_entity_label": resolved_entity_label,
            "resolved_entity_id": resolved_entity_id,
            "resolved_entity_type": resolved_entity_type,
        },
        "recommendations": [],
        "confidence": 0.9,
    }


def list_supported_intents() -> list[str]:
    return ["nl_query"]