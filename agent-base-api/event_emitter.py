"""Internal event emitter — sisteme içeriden event üretmek için.

Kullanım:
    from event_emitter import emit_event
    event_id = emit_event("campaign.created", {"campaign_id": 42, "name": "Yaz Kampanyası"})

Davranış:
    1. fake_ai_api.db.timeline tablosuna INSERT — listener.py 2sn polling
       ile bu satırı görür ve process_event içine sokar.
    2. EXTERNAL_WEBHOOK_URL env değişkeni doluysa, aynı payload'u POST
       eder (timeout 10s, başarısızlık event_emit'i bozmaz — sadece log).

Format:
    event_type "campaign.created" → log_group="campaign", event="created".
    Eğer noktasız bir ad geçilirse log_group="event", event=event_type olarak
    kabul edilir.

Geri dönüş:
    Başarılıysa int (yeni timeline.id), aksi halde 0.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from typing import Any


_DEFAULT_DB_PATH = os.environ.get("FAKE_API_DB_PATH", "fake_ai_api.db")


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _split_event_type(event_type: str) -> tuple[str, str]:
    """'campaign.created' → ('campaign', 'created'). Noktasızsa default 'event'."""
    s = (event_type or "").strip().lower()
    if "." in s:
        head, _, tail = s.partition(".")
        return (head or "event", tail or "occurred")
    return ("event", s or "occurred")


def emit_event(
    event_type: str,
    payload: dict[str, Any] | None = None,
    *,
    source: str = "internal",
    subject_type: str | None = None,
    subject_id: int | None = None,
    store_id: int | None = None,
    description: str | None = None,
    db_path: str | None = None,
) -> int:
    """Sisteme içeriden bir event yaz.

    Returns:
        Başarıyla yazılan timeline.id (>0) veya hata durumunda 0.
    """
    log_group, event_name = _split_event_type(event_type)
    payload_dict = dict(payload or {})

    # subject_type / subject_id payload'tan tahmin et (eğer caller vermediyse)
    if subject_type is None:
        subject_type = payload_dict.get("subject_type") or _GUESS_SUBJECT_TYPE.get(log_group)
    if subject_id is None:
        subject_id = payload_dict.get("subject_id") or payload_dict.get(f"{log_group}_id")
    if store_id is None:
        store_id = payload_dict.get("store_id")

    description = description or f"emit:{event_type}"

    meta = {
        "processed_by_rule_engine": False,
        "source": source,
        "priority": "normal",
        "emitted_via": "event_emitter.emit_event",
        "orchestration": {
            "path": "pending",
            "route": "unprocessed",
            "skip_reason": "listener bekleniyor",
        },
    }

    new_id = 0
    target_db = db_path or _DEFAULT_DB_PATH
    try:
        conn = sqlite3.connect(target_db)
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO timeline (
                ts,
                event, event_label,
                log_group, group_label,
                description,
                store_id,
                subject_type, subject_id,
                causer_type, causer_id, causer_name,
                changes, payload, meta
            )
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                _now_iso(),
                event_name,
                event_name.title(),
                log_group,
                log_group.title(),
                description,
                store_id,
                subject_type,
                int(subject_id) if isinstance(subject_id, (int, str)) and str(subject_id).isdigit() else None,
                "system",
                1,
                f"internal:{source}",
                json.dumps({}, ensure_ascii=False),
                json.dumps(payload_dict, ensure_ascii=False, default=str),
                json.dumps(meta, ensure_ascii=False),
            ),
        )
        new_id = int(c.lastrowid or 0)
        conn.commit()
        conn.close()
    except Exception as exc:
        print(f"[event_emitter] DB INSERT failed: {exc}")
        return 0

    # Webhook (best-effort, hatalar emit'i bozmaz)
    webhook = (os.environ.get("EXTERNAL_WEBHOOK_URL") or "").strip()
    if webhook:
        try:
            import requests
            requests.post(
                webhook,
                json={
                    "timeline_id": new_id,
                    "event_type": event_type,
                    "source": source,
                    "payload": payload_dict,
                },
                timeout=float(os.environ.get("EVENT_EMITTER_WEBHOOK_TIMEOUT", "10")),
            )
        except Exception as exc:
            print(f"[event_emitter] webhook POST failed: {exc}")

    return new_id


# log_group → subject_type tahmin tablosu (caller vermediyse).
_GUESS_SUBJECT_TYPE: dict[str, str] = {
    "store":    "Store",
    "product":  "Item",
    "order":    "Order",
    "stock":    "Item",
    "review":   "Review",
    "customer": "Customer",
    "campaign": "Campaign",
    "banner":   "Banner",
    "sales":    "Sales",
    "shipping": "Order",
    "story":    "Story",    # Bölüm 6
    "coupon":   "Coupon",   # Bölüm 6
}
