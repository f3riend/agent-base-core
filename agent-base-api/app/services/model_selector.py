"""Model seçici — intent + tier + admin durumuna göre OpenAI model adı döndürür."""
from __future__ import annotations

import os


_MINI = os.environ.get("BCHAT_MINI_MODEL", "gpt-4o-mini")
_FULL = os.environ.get("BCHAT_FULL_MODEL", "gpt-4o")


def select_model(
    *,
    intent: str,
    model_tier: str,
    is_admin: bool,
    mention_scope: str,
    api_key: str | None = None,
) -> str:
    """
    model_tier="full" → gpt-4o
    admin + scope="all" → gpt-4o (platform geneli analiz)
    diğer → gpt-4o-mini
    """
    if (model_tier or "").lower() == "full":
        return _FULL
    if is_admin and mention_scope == "all":
        return _FULL
    return _MINI
