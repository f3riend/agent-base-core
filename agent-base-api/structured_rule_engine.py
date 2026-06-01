"""
StructuredRule engine — tetik olayını match eden kurallara graph kickoff yapar.

Bu modül listener ile LangGraph runtime arasındaki köprü. Mevcut
rule_engine.py + autonomous_planner yolu OLDUĞU GİBİ devam ediyor; bu
modül onlara EK olarak çalışır. Listener tarafına eklendiğinde her olay
hem eski path'ten hem de bu structured rule path'inden geçer — kurallar
birbirini bozmaz.

Kayıt/güncelleme/silme + match + execute helpers.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from db import db_connection, execute_query, execute_write, now_iso
from structured_rule import StructuredRule


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def save_rule(rule: StructuredRule, *, new_version: bool = False) -> StructuredRule:
    """Yeni veya güncellenmiş kuralı persist et.

    Tur 2 davranışı:
        - rule.id YOKSA: yeni satır, version=1, is_current=1.
        - rule.id VAR + new_version=False: in-place update (eski davranış).
        - rule.id VAR + new_version=True: eski satır is_current=0 yapılır
          ve supersedes_at set edilir; yeni satır parent_rule_id'yi
          işaret edip version=eski+1, is_current=1 olarak insert edilir.
    """
    ts = now_iso()
    payload = json.dumps(rule.to_storage_dict(), ensure_ascii=False, default=str)
    trigger_event = rule.trigger.event_type

    if rule.id and not new_version:
        # In-place update — sürüm değiştirme
        execute_write(
            """
            UPDATE structured_rules
            SET name=?, natural_language=?, rule_json=?, trigger_event=?,
                enabled=?, parse_confidence=?, updated_at=?
            WHERE id=?
            """,
            (
                rule.name, rule.natural_language, payload, trigger_event,
                1 if rule.enabled else 0,
                float(rule.parse_confidence),
                ts, int(rule.id),
            ),
        )
        rule_id = int(rule.id)
    elif rule.id and new_version:
        # Yeni sürüm — eski versionu inactive yap
        old_row = execute_query(
            "SELECT version FROM structured_rules WHERE id=?",
            (int(rule.id),), one=True,
        )
        old_version = int(old_row["version"]) if old_row else 1
        execute_write(
            """
            UPDATE structured_rules
            SET is_current=0, supersedes_at=?, enabled=0, updated_at=?
            WHERE id=?
            """,
            (ts, ts, int(rule.id)),
        )
        rule_id = execute_write(
            """
            INSERT INTO structured_rules (
                user_id, org_id, name, natural_language, rule_json,
                trigger_event, enabled, parse_confidence,
                version, parent_rule_id, is_current,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                int(rule.user_id), rule.org_id,
                rule.name, rule.natural_language, payload, trigger_event,
                1 if rule.enabled else 0,
                float(rule.parse_confidence),
                old_version + 1, int(rule.id),
                ts, ts,
            ),
        )
    else:
        # Hiç var olmayan, sıfırdan yeni kural
        rule_id = execute_write(
            """
            INSERT INTO structured_rules (
                user_id, org_id, name, natural_language, rule_json,
                trigger_event, enabled, parse_confidence,
                version, is_current, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?)
            """,
            (
                int(rule.user_id), rule.org_id,
                rule.name, rule.natural_language, payload, trigger_event,
                1 if rule.enabled else 0,
                float(rule.parse_confidence),
                ts, ts,
            ),
        )

    row = execute_query(
        "SELECT * FROM structured_rules WHERE id=?",
        (rule_id,), one=True,
    )
    return StructuredRule.from_storage(dict(row))


def list_rule_versions(parent_rule_id: int) -> list[dict]:
    """Belirli bir kuralın tüm sürümlerini (en yenisi önce) döndür."""
    rows = execute_query(
        """
        SELECT id, version, is_current, enabled, name, natural_language,
               parse_confidence, created_at, updated_at, supersedes_at
        FROM structured_rules
        WHERE id=? OR parent_rule_id=? OR id IN (
            SELECT parent_rule_id FROM structured_rules WHERE id=?
        )
        ORDER BY version DESC, id DESC
        """,
        (int(parent_rule_id), int(parent_rule_id), int(parent_rule_id)),
    )
    return [dict(r) for r in rows]


def detect_conflicts(user_id: int) -> list[dict]:
    """Aynı (trigger_event, target_handle, channel) için birden fazla aktif
    kural varsa conflict raporu döndür.

    UI'da uyarı badge'i bunun döndürdüğü conflict_id listesinden gelir.
    """
    rules = list_rules(user_id=user_id, enabled_only=True, limit=500)
    bucket: dict[tuple, list[StructuredRule]] = {}
    for r in rules:
        handle = (r.target.account_handle or "").lower()
        channel = r.content.channel or "instagram"
        key = (r.trigger.event_type, handle, channel)
        bucket.setdefault(key, []).append(r)

    conflicts: list[dict] = []
    for (event_type, handle, channel), group in bucket.items():
        if len(group) > 1:
            conflicts.append({
                "trigger_event": event_type,
                "account_handle": handle or "(belirsiz)",
                "channel": channel,
                "rule_count": len(group),
                "rule_ids": [r.id for r in group],
                "rule_names": [r.name for r in group],
                "severity": "high" if len(group) >= 3 else "medium",
                "summary": (
                    f"{event_type} olayı için {handle or 'genel'} hesabında "
                    f"{channel} kanalında {len(group)} aktif kural var; "
                    f"çatışabilirler."
                ),
            })
    return conflicts


def get_rule(rule_id: int) -> StructuredRule | None:
    row = execute_query(
        "SELECT * FROM structured_rules WHERE id=?",
        (int(rule_id),),
        one=True,
    )
    return StructuredRule.from_storage(dict(row)) if row else None


def list_rules(
    *,
    user_id: int,
    enabled_only: bool = False,
    include_versions: bool = False,
    limit: int = 100,
) -> list[StructuredRule]:
    """Default: sadece en güncel (is_current=1) sürümleri döndürür."""
    sql = "SELECT * FROM structured_rules WHERE user_id=?"
    params: list[Any] = [int(user_id)]
    if not include_versions:
        sql += " AND COALESCE(is_current, 1) = 1"
    if enabled_only:
        sql += " AND enabled=1"
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit))
    rows = execute_query(sql, tuple(params))
    return [StructuredRule.from_storage(dict(r)) for r in rows]


def set_enabled(rule_id: int, enabled: bool) -> bool:
    n = execute_write(
        "UPDATE structured_rules SET enabled=?, updated_at=? WHERE id=?",
        (1 if enabled else 0, now_iso(), int(rule_id)),
    )
    return bool(n)


def delete_rule(rule_id: int) -> bool:
    n = execute_write(
        "DELETE FROM structured_rules WHERE id=?",
        (int(rule_id),),
    )
    return bool(n)


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def find_matching_rules(
    event_type: str,
    event_payload: dict | None = None,
    *,
    user_id: int | None = None,
) -> list[StructuredRule]:
    """Verilen olay tipine match eden enabled kuralları döndür.

    user_id verilirse o kullanıcının kurallarına daraltır. Aksi halde
    tüm kullanıcılar — listener tipik olarak event'in store'undan
    user_id'yi resolve eder ve onu paslar.
    """
    sql = """
        SELECT * FROM structured_rules
        WHERE trigger_event=? AND enabled=1
          AND COALESCE(is_current, 1) = 1
    """
    params: list[Any] = [event_type]
    if user_id is not None:
        sql += " AND user_id=?"
        params.append(int(user_id))
    sql += " ORDER BY id ASC"

    rows = execute_query(sql, tuple(params))
    matching: list[StructuredRule] = []
    for row in rows:
        rule = StructuredRule.from_storage(dict(row))
        if _filters_match(rule, event_payload or {}):
            matching.append(rule)
    return matching


def _filters_match(rule: StructuredRule, payload: dict) -> bool:
    """trigger.filters + target.entity_filters için sade dict match.

    Anahtar payload içinde olmalı + değer eşit olmalı. Anahtar
    bulunmuyorsa eşleşme başarısız (default veriler hep en
    permissive durumunu temsil eder).
    """
    filters = {
        **(rule.trigger.filters or {}),
        **(rule.target.entity_filters or {}),
    }
    if not filters:
        return True
    for key, expected in filters.items():
        actual = _resolve_nested(payload, key)
        if actual is None:
            return False
        if isinstance(expected, str):
            if str(actual).lower() != expected.lower():
                return False
        elif actual != expected:
            return False
    return True


def _resolve_nested(payload: dict, dotted_key: str) -> Any:
    """`store.handle` gibi dotted key'i payload'tan oku."""
    parts = dotted_key.split(".")
    cur: Any = payload
    for p in parts:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


# ---------------------------------------------------------------------------
# Execute on matching event
# ---------------------------------------------------------------------------


def trigger_rules_for_event(
    event_type: str,
    event: dict,
    *,
    user_id: int | None = None,
) -> list[dict]:
    """Bir olay için match eden tüm kuralları graph üzerinden başlat.

    Returns:
        Her kural için {rule_id, execution_id, status, current_node, ...}
        listesi.
    """
    matching = find_matching_rules(
        event_type, event.get("payload") or {}, user_id=user_id,
    )
    if not matching:
        return []

    from langgraph_engine.runtime import start_execution

    results: list[dict] = []
    for rule in matching:
        try:
            res = start_execution(
                rule=rule, event=event, user_id=user_id or rule.user_id,
            )
            results.append(res)
            _mark_fired(rule.id)
        except Exception as exc:
            print(f"[STRUCT_RULE_ENGINE] start_execution failed for rule {rule.id}: {exc}")
            results.append({
                "rule_id": rule.id,
                "execution_id": None,
                "status": "failed",
                "error": str(exc)[:200],
            })
    return results


def _mark_fired(rule_id: int | None) -> None:
    if not rule_id:
        return
    execute_write(
        """
        UPDATE structured_rules
        SET last_fired_at=?, fire_count=COALESCE(fire_count,0)+1
        WHERE id=?
        """,
        (now_iso(), int(rule_id)),
    )
