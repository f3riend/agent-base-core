"""
Multi-tenant rule service — DB-backed rules with in-memory compiled cache.

Architecture:
  listener → rule_service.get_compiled_rules(user_id)
           → cache hit  → compiled rules
           → cache miss → DB fetch → parse once → cache
           → rule_engine.find_matching_rules(..., rules=compiled)
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

from db import DEFAULT_USER_ID, db_connection, execute_query, execute_write, now_iso
from rule_engine import parse_rules_content

RULES_PATH = os.environ.get("RULES_PATH", "rules.txt")

# In-memory cache: user_id → { rules, loaded_at, version }
RULE_CACHE: dict[int, dict[str, Any]] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_VERSION: dict[int, int] = {}


def _cache_version(user_id: int) -> int:
    return _CACHE_VERSION.get(user_id, 0)


def invalidate_cache(user_id: int | None = None):
    """Invalidate on create/update/delete/disable."""
    with _CACHE_LOCK:
        if user_id is None:
            RULE_CACHE.clear()
            print("[RULE CACHE] invalidated ALL")
        elif user_id in RULE_CACHE:
            del RULE_CACHE[user_id]
            _CACHE_VERSION[user_id] = _CACHE_VERSION.get(user_id, 0) + 1
            print(f"[RULE CACHE] invalidated user_id={user_id}")


def _fetch_rules_from_db(user_id: int) -> list[dict]:
    rows = execute_query(
        """
        SELECT id, user_id, rule_name, natural_language, dsl, enabled,
               created_at, updated_at
        FROM rules
        WHERE user_id=? AND enabled=1
        ORDER BY id ASC
        """,
        (user_id,),
    )
    return [dict(row) for row in rows]


def _compile_rule_row(row: dict) -> dict | None:
    dsl = row.get("dsl", "").strip()
    if not dsl:
        return None

    parsed_list = parse_rules_content(dsl)
    if not parsed_list:
        return None

    compiled = parsed_list[0]
    compiled["id"] = row["id"]
    compiled["user_id"] = row["user_id"]
    compiled["rule_name"] = row["rule_name"]
    compiled["natural_language"] = row.get("natural_language")
    compiled["enabled"] = bool(row.get("enabled", 1))
    return compiled


def load_and_compile_rules(user_id: int) -> list[dict]:
    """Fetch from DB and compile DSL once."""
    rows = _fetch_rules_from_db(user_id)
    compiled = []

    for row in rows:
        rule = _compile_rule_row(row)
        if rule:
            compiled.append(rule)

    print(
        f"[RULE SERVICE] compiled {len(compiled)} rule(s) "
        f"for user_id={user_id}"
    )
    return compiled


def get_compiled_rules(user_id: int = DEFAULT_USER_ID) -> list[dict]:
    """
    Cache hit → return compiled rules from memory.
    Cache miss → DB fetch, parse once, store in cache.
    """
    user_id = int(user_id)
    version = _cache_version(user_id)

    with _CACHE_LOCK:
        cached = RULE_CACHE.get(user_id)
        if cached and cached.get("version") == version:
            print(f"[RULE CACHE] hit user_id={user_id} rules={len(cached['rules'])}")
            return cached["rules"]

    rules = load_and_compile_rules(user_id)

    with _CACHE_LOCK:
        RULE_CACHE[user_id] = {
            "rules": rules,
            "loaded_at": time.monotonic(),
            "version": version,
        }

    print(f"[RULE CACHE] miss user_id={user_id} loaded={len(rules)}")
    return rules


def list_rules(user_id: int = DEFAULT_USER_ID, include_disabled: bool = False):
    if include_disabled:
        rows = execute_query(
            "SELECT * FROM rules WHERE user_id=? ORDER BY id ASC",
            (user_id,),
        )
    else:
        rows = execute_query(
            "SELECT * FROM rules WHERE user_id=? AND enabled=1 ORDER BY id ASC",
            (user_id,),
        )
    return [dict(r) for r in rows]


def get_rule(rule_id: int):
    row = execute_query(
        "SELECT * FROM rules WHERE id=?", (rule_id,), one=True
    )
    return dict(row) if row else None


def upsert_rule(
    user_id: int,
    rule_name: str,
    dsl: str,
    natural_language: str | None = None,
    enabled: bool = True,
) -> int:
    ts = now_iso()
    existing = execute_query(
        """
        SELECT id FROM rules WHERE user_id=? AND rule_name=?
        """,
        (user_id, rule_name),
        one=True,
    )

    with db_connection() as conn:
        if existing:
            conn.execute(
                """
                UPDATE rules
                SET dsl=?, natural_language=?, enabled=?, updated_at=?
                WHERE id=?
                """,
                (
                    dsl,
                    natural_language,
                    1 if enabled else 0,
                    ts,
                    existing["id"],
                ),
            )
            rule_id = existing["id"]
        else:
            cursor = conn.execute(
                """
                INSERT INTO rules (
                    user_id, rule_name, natural_language, dsl,
                    enabled, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    rule_name,
                    natural_language,
                    dsl,
                    1 if enabled else 0,
                    ts,
                    ts,
                ),
            )
            rule_id = cursor.lastrowid

    invalidate_cache(user_id)
    return rule_id


def delete_rule(rule_id: int, user_id: int | None = None):
    row = execute_query("SELECT user_id FROM rules WHERE id=?", (rule_id,), one=True)
    if not row:
        return False
    uid = user_id or row["user_id"]
    execute_write("DELETE FROM rules WHERE id=?", (rule_id,))
    invalidate_cache(uid)
    return True


def set_rule_enabled(rule_id: int, enabled: bool):
    row = execute_query("SELECT user_id FROM rules WHERE id=?", (rule_id,), one=True)
    if not row:
        return False
    execute_write(
        "UPDATE rules SET enabled=?, updated_at=? WHERE id=?",
        (1 if enabled else 0, now_iso(), rule_id),
    )
    invalidate_cache(row["user_id"])
    return True


def save_rules_batch(
    user_id: int,
    rules: list[dict],
    natural_language: str | None = None,
):
    """Persist compiled rule dicts to DB."""
    from rule_manager import format_rule_block

    for rule in rules:
        dsl = format_rule_block(rule)
        upsert_rule(
            user_id=user_id,
            rule_name=rule["name"],
            dsl=dsl,
            natural_language=natural_language,
        )


def export_to_file(user_id: int = DEFAULT_USER_ID, path: str = RULES_PATH):
    """Optional export layer — rules.txt is NOT primary."""
    from rule_manager import format_rule_block

    rows = execute_query(
        "SELECT dsl FROM rules WHERE user_id=? AND enabled=1 ORDER BY id",
        (user_id,),
    )
    blocks = [row["dsl"] for row in rows if row["dsl"]]
    content = "\n\n".join(blocks) + ("\n" if blocks else "")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return content


def import_from_file(
    user_id: int = DEFAULT_USER_ID,
    path: str = RULES_PATH,
):
    """Import rules.txt into DB for a tenant."""
    if not os.path.exists(path):
        return 0

    with open(path, "r", encoding="utf-8-sig") as f:
        content = f.read()

    parsed = parse_rules_content(content)
    count = 0
    for rule in parsed:
        from rule_manager import format_rule_block
        upsert_rule(
            user_id=user_id,
            rule_name=rule["name"],
            dsl=format_rule_block(rule),
            natural_language="imported from rules.txt",
        )
        count += 1

    invalidate_cache(user_id)
    return count


def seed_rules_from_file_if_empty():
    """Bootstrap DB from rules.txt on first run."""
    row = execute_query("SELECT COUNT(*) as c FROM rules", one=True)
    if row and row["c"] > 0:
        return

    if os.path.exists(RULES_PATH):
        count = import_from_file(DEFAULT_USER_ID, RULES_PATH)
        print(f"[RULE SERVICE] seeded {count} rule(s) from {RULES_PATH}")


def resolve_user_id_from_event(
    event: dict,
    subject_type: str,
    subject_id: int,
) -> int:
    """
    Map timeline event to tenant user_id.

    Öncelik sırası:
        1. event.causer_id  — timeline kolonu, fake_commerce_app.create_*'tan
           geliyor. En güvenilir sinyal (mağaza listener.db'ye sync edilmemiş
           olsa bile doğru user'a yönlendirir).
        2. event.payload.user_id — JSON string olabilir, normalize edilir.
        3. listener.db.stores.user_id — eski yol (store_id veya Store subject).
        4. listener.db.items.user_id veya parent store.
        5. DEFAULT_USER_ID — son çare.
    """
    from db import DEFAULT_USER_ID

    # 1) Event payload'tan causer_id direkt al — en güvenilir sinyal
    causer_id = event.get("causer_id")
    if causer_id and int(causer_id) > 0:
        return int(causer_id)

    # 2) Payload içinde user_id var mı?
    payload = event.get("payload") or {}
    if isinstance(payload, str):
        import json
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}
    payload_user_id = payload.get("user_id")
    if payload_user_id and int(payload_user_id) > 0:
        return int(payload_user_id)

    # 3) listener.db stores tablosundan bak
    store_id = event.get("store_id")
    if store_id:
        row = execute_query(
            "SELECT user_id FROM stores WHERE id=?",
            (store_id,),
            one=True,
        )
        if row and row["user_id"]:
            return int(row["user_id"])

    if subject_type == "Store":
        row = execute_query(
            "SELECT user_id FROM stores WHERE id=?",
            (subject_id,),
            one=True,
        )
        if row and row["user_id"]:
            return int(row["user_id"])

    if subject_type == "Item":
        row = execute_query(
            "SELECT user_id, store_id FROM items WHERE id=?",
            (subject_id,),
            one=True,
        )
        if row:
            if row["user_id"]:
                return int(row["user_id"])
            if row["store_id"]:
                srow = execute_query(
                    "SELECT user_id FROM stores WHERE id=?",
                    (row["store_id"],),
                    one=True,
                )
                if srow and srow["user_id"]:
                    return int(srow["user_id"])

    return DEFAULT_USER_ID


def get_cache_stats() -> dict:
    with _CACHE_LOCK:
        return {
            "cached_users": list(RULE_CACHE.keys()),
            "entries": {
                uid: {
                    "rule_count": len(data["rules"]),
                    "loaded_at": data["loaded_at"],
                    "version": data["version"],
                }
                for uid, data in RULE_CACHE.items()
            },
        }
