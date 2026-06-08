"""Rate limiter — Redis sliding-window, kullanıcı başına dakikalık limit.

Satıcı: 20/dk
Admin:  60/dk
Redis yoksa (True, 999) — block etme.
"""
from __future__ import annotations

import time

from app.services.chat_cache import _get_redis


_SELLER_LIMIT = 20
_ADMIN_LIMIT = 60
_WINDOW_SEC = 60


def check_rate_limit(user_id: int, is_admin: bool) -> tuple[bool, int]:
    """Returns (allowed, remaining).

    Redis kullanılamıyorsa (True, 999) — sistem hiç durmamalı.
    """
    limit = _ADMIN_LIMIT if is_admin else _SELLER_LIMIT
    r = _get_redis()
    if r is None:
        return True, 999

    key = f"bchat:rl:{int(user_id)}"
    now = time.time()
    cutoff = now - _WINDOW_SEC

    try:
        pipe = r.pipeline()
        pipe.zremrangebyscore(key, 0, cutoff)
        pipe.zadd(key, {f"{now}:{int((now % 1) * 1_000_000)}": now})
        pipe.zcard(key)
        pipe.expire(key, _WINDOW_SEC + 5)
        _, _, count, _ = pipe.execute()
        remaining = max(0, limit - int(count))
        return (int(count) <= limit), remaining
    except Exception as exc:
        print(f"[RATE_LIMIT] failed open: {exc}")
        return True, 999
