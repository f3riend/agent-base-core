"""Business chat Redis cache — TTL yönetimi, hata olursa sessiz geç."""
from __future__ import annotations

import os
from typing import Any

_DEFAULT_TTL = 120

_redis_client: Any = None
_init_attempted = False


def _get_redis():
    global _redis_client, _init_attempted
    if _init_attempted:
        return _redis_client
    _init_attempted = True
    try:
        import redis
        url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        client = redis.Redis.from_url(url, socket_timeout=1, socket_connect_timeout=1)
        client.ping()
        _redis_client = client
    except Exception as exc:
        print(f"[CHAT_CACHE] redis unavailable: {exc}")
        _redis_client = None
    return _redis_client


def _key(user_id: int, scope_hash: str) -> str:
    return f"bchat:{int(user_id)}:{scope_hash}"


def get_cached(user_id: int, intent: str, scope_hash: str) -> str | None:
    r = _get_redis()
    if r is None:
        return None
    try:
        raw = r.get(_key(user_id, scope_hash))
        if not raw:
            return None
        return raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
    except Exception as exc:
        print(f"[CHAT_CACHE] get failed: {exc}")
        return None


def set_cache(
    user_id: int,
    intent: str,
    scope_hash: str,
    value: str,
    row_count: int = 1,
) -> None:
    """Cache'e yaz. row_count=0 ise yazma — yanlış "veri yok" cevabı cache'e girmesin."""
    r = _get_redis()
    if r is None or not value:
        return
    if row_count == 0:
        return
    try:
        r.setex(_key(user_id, scope_hash), _DEFAULT_TTL, value)
    except Exception as exc:
        print(f"[CHAT_CACHE] set failed: {exc}")


def invalidate_user(user_id: int) -> None:
    """Kullanıcının tüm cache entry'lerini sil."""
    r = _get_redis()
    if r is None:
        return
    try:
        pattern = f"bchat:{int(user_id)}:*"
        keys = list(r.scan_iter(match=pattern, count=200))
        if keys:
            r.delete(*keys)
    except Exception as exc:
        print(f"[CHAT_CACHE] invalidate failed: {exc}")