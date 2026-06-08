"""
Business query router — nl_to_sql tabanlı dinamik sorgu sistemi.

Akış: mention → rate-limit → access → cache → nl_to_sql → cache yaz → döndür
Güvenlik: nl_to_sql sadece SELECT geçirir, UUID cast garantili.
"""
from __future__ import annotations

import hashlib
import os
from typing import Optional


def route(
    question: str,
    *,
    user_id: int = 1,
    active_entity_label: str = "",
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
            },
            "op_context": {},
            "model_override": model,
            "from_cache": False,
            "mention_scope": mention.scope,
            "store_ids": store_ids,
        },
        "recommendations": [],
        "confidence": 0.9,
    }


def list_supported_intents() -> list[str]:
    return ["nl_query"]