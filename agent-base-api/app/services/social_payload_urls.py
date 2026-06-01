"""Sosyal doküman JSON icindeki eski medya URL'lerini yeni public köke cevir (API cevabi)."""

from __future__ import annotations

import copy
import os
from typing import Any


def _legacy_url_prefixes() -> list[str]:
    raw = (os.getenv("R2_LEGACY_PUBLIC_URL_PREFIXES") or "").strip()
    if raw:
        return [p.strip().rstrip("/") for p in raw.split(",") if p.strip()]
    return ["https://test.r2.dev", "http://test.r2.dev"]


def _replacement_public_base() -> str | None:
    b = (os.getenv("R2_PUBLIC_BASE_URL") or "").strip().rstrip("/")
    return b or None


def rewrite_legacy_media_urls_in_payload(data: Any) -> Any:
    """Eski R2/public host (ornek test.r2.dev) -> R2_PUBLIC_BASE_URL; DB'ye yazmaz, yalnizca cevap."""
    new_base = _replacement_public_base()
    if not new_base:
        return data

    def walk(v: Any) -> Any:
        if isinstance(v, str):
            s = v
            for old in _legacy_url_prefixes():
                prefix = old.rstrip("/")
                if not prefix:
                    continue
                if s == prefix:
                    return new_base
                if s.startswith(prefix + "/"):
                    rest = s[len(prefix) + 1 :].lstrip("/")
                    return f"{new_base}/{rest}" if rest else new_base
            return v
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        return v

    return walk(copy.deepcopy(data))
