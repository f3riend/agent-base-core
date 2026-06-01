"""
Fake-platform internal service layer.

This module owns the canonical implementation of the "create X / update X"
operations that previously lived inside the FastAPI `/internal/*` route
handlers in main.py. Both surfaces consume it:

  - main.py `/internal/*` endpoints → thin wrappers that call functions here.
  - fake_data_generator, business_activity_simulator, product_import_service,
    orchestration_api (seed-data / import) → direct in-process calls.

Why this module exists:
  fake_data_generator used to `requests.post("http://127.0.0.1:8000/internal/...")`
  back into the SAME FastAPI process. That is a self-referential HTTP loop —
  every seed turn occupied a worker thread on its own server, causing
  timeouts, worker starvation, and deadlock-shaped behavior. We always have
  the option to call the function directly; HTTP into your own process is
  never the right tool.

Rules of the road:
  - This module talks ONLY to fake_ai_api.db (the fake platform's storage).
  - It emits timeline events in the same shape the listener expects.
  - It does NOT import main.py (would create a circular dependency).
  - It is safe to call from any FastAPI route, any worker, any test.

If you find yourself reaching for `requests.post("http://127.0.0.1:.../internal/...")`
inside this process again, call the matching function here instead — and
consider invoking `assert_not_internal_http_loop(url)` to catch the next
regression.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse


FAKE_API_DB_PATH = os.environ.get("FAKE_API_DB_PATH", "fake_ai_api.db")


# Hosts that resolve back to this process. Used by assert_not_internal_http_loop
# to refuse self-referential HTTP calls inside the FastAPI runtime.
_SELF_HOSTS = frozenset({
    "127.0.0.1",
    "localhost",
    "0.0.0.0",
    "::1",
})


class InternalHTTPLoopError(RuntimeError):
    """Raised when in-process code is about to HTTP into its own server."""


def assert_not_internal_http_loop(url: str) -> None:
    """Guard against self-referential HTTP calls from inside the API process.

    Call this before any `requests.*(url)` in code paths that may execute
    inside the FastAPI process. If `url` points back at this server, raise
    so the caller picks the in-process service function instead.

    The check is process-level via `INTERNAL_SERVICE_IN_PROCESS=1`, which
    main.py sets at import time. CLI scripts and the worker processes never
    set it, so they remain free to use HTTP as a client.
    """
    if os.environ.get("INTERNAL_SERVICE_IN_PROCESS") != "1":
        return
    try:
        parsed = urlparse(url)
    except Exception:
        return
    host = (parsed.hostname or "").lower()
    if host in _SELF_HOSTS:
        raise InternalHTTPLoopError(
            f"Refusing self-referential HTTP call to {url!r} from inside the "
            "API process. Use the matching function in internal_service.py."
        )


# ---------------------------------------------------------------------------
# DB + timeline helpers (local to this module — does not import main.py)
# ---------------------------------------------------------------------------


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(FAKE_API_DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def _now() -> str:
    return datetime.utcnow().isoformat()


_GROUP_LABELS = {
    "store": "Stores",
    "product": "Products",
    "order": "Orders",
    "stock": "Stocks",
    "banner": "Banners",
    "review": "Reviews",
    "customer": "Customers",
    "shipping": "Shipping",
    "sales": "Sales",
    "campaign": "Campaigns",
}

_EVENT_LABELS = {
    "created": "Created",
    "updated": "Updated",
    "deleted": "Deleted",
    "status_changed": "Status Changed",
    "negative": "Negative",
    "delayed": "Delayed",
}


def emit_timeline_event(
    *,
    event: str,
    log_group: str,
    description: str,
    store_id: Optional[int] = None,
    subject_type: Optional[str] = None,
    subject_id: Optional[int] = None,
    payload: Optional[dict] = None,
    changes: Optional[dict] = None,
    causer_id: int = 1,
) -> int:
    """Write one row to the fake-platform timeline. Returns the event_id.

    `causer_id`: timeline.causer_id kolonu — multi-tenant için. Default 1
    (legacy). create_store/create_product user_id'yi geçirir.
    """
    meta = {
        "processed_by_rule_engine": False,
        "orchestration": {
            "path": "pending",
            "route": "unprocessed",
            "skip_reason": "listener bekleniyor",
        },
        "source": "fake_platform",
    }
    with _conn() as conn:
        cursor = conn.execute(
            """
            INSERT INTO timeline (
                ts, event, event_label, log_group, group_label,
                description, store_id, subject_type, subject_id,
                causer_type, causer_id, causer_name,
                changes, payload, meta
            )
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                _now(),
                event,
                _EVENT_LABELS.get(event, event),
                log_group,
                _GROUP_LABELS.get(log_group, log_group),
                description,
                store_id,
                subject_type,
                subject_id,
                "system",
                int(causer_id),
                f"user:{int(causer_id)}",
                json.dumps(changes or {}),
                json.dumps(payload or {}, ensure_ascii=False),
                json.dumps(meta),
            ),
        )
        event_id = cursor.lastrowid
        conn.commit()
    return event_id


# ---------------------------------------------------------------------------
# Service operations — one function per /internal/* endpoint
# ---------------------------------------------------------------------------


def create_store(
    *,
    name: str,
    owner: str,
    instagram: Optional[str] = None,
    logo_url: Optional[str] = None,
    banner_url: Optional[str] = None,
    theme_color: Optional[str] = None,
    user_id: int = 1,
) -> dict:
    ts = _now()
    with _conn() as conn:
        cursor = conn.execute(
            """
            INSERT INTO stores (
                name, owner, instagram,
                logo_url, banner_url, theme_color,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (name, owner, instagram, logo_url, banner_url, theme_color, ts, ts),
        )
        store_id = cursor.lastrowid
        conn.commit()

    event_id = emit_timeline_event(
        event="created",
        log_group="store",
        description=f"Store created: {name}",
        store_id=store_id,
        subject_type="Store",
        subject_id=store_id,
        payload={
            "name": name, "owner": owner, "instagram": instagram,
            "logo_url": logo_url, "banner_url": banner_url,
            "theme_color": theme_color,
            "user_id": int(user_id),
        },
        causer_id=int(user_id),
    )
    return {"id": store_id, "event_id": event_id}


def create_product(
    *,
    store_id: int,
    name: str,
    price: float,
    stock: int = 0,
    category: Optional[str] = None,
    image_url: Optional[str] = None,
    images: Optional[list] = None,
    discount_percent: Optional[float] = None,
    user_id: int = 1,
) -> dict:
    import json as _json
    ts = _now()
    images_json = _json.dumps(images) if isinstance(images, list) and images else None
    with _conn() as conn:
        cursor = conn.execute(
            """
            INSERT INTO items (
                store_id, name, price, stock, sales, category,
                image_url, images_json, discount_percent,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                store_id, name, price, stock, 0, category,
                image_url, images_json,
                float(discount_percent) if discount_percent is not None else 0.0,
                ts, ts,
            ),
        )
        item_id = cursor.lastrowid
        conn.commit()

    event_id = emit_timeline_event(
        event="created",
        log_group="product",
        description=f"Product created: {name}",
        store_id=store_id,
        subject_type="Item",
        subject_id=item_id,
        payload={
            "store_id": store_id,
            "name": name,
            "price": price,
            "stock": stock,
            "category": category,
            "image_url": image_url,
            "image_urls": images if isinstance(images, list) else None,
            "primary_image_url": image_url,
            "discount_percent": discount_percent,
            "user_id": int(user_id),
        },
        causer_id=int(user_id),
    )
    return {"id": item_id, "event_id": event_id}


def create_order(*, store_id: int, item_id: int, quantity: int) -> dict:
    with _conn() as conn:
        item = conn.execute(
            "SELECT * FROM items WHERE id=?", (item_id,)
        ).fetchone()
        if not item:
            raise ValueError(f"item not found: {item_id}")

        old_stock = item["stock"]
        new_stock = old_stock - quantity
        ts = _now()

        cursor = conn.execute(
            """
            INSERT INTO orders (
                store_id, item_id, quantity, status, created_at, updated_at
            )
            VALUES (?, ?, ?, 'pending', ?, ?)
            """,
            (store_id, item_id, quantity, ts, ts),
        )
        order_id = cursor.lastrowid

        conn.execute(
            """
            UPDATE items SET stock=?, sales=sales+?, updated_at=?
            WHERE id=?
            """,
            (new_stock, quantity, ts, item_id),
        )
        conn.commit()

    order_event_id = emit_timeline_event(
        event="created",
        log_group="order",
        description=f"Order created #{order_id}",
        store_id=store_id,
        subject_type="Order",
        subject_id=order_id,
        payload={"store_id": store_id, "item_id": item_id, "quantity": quantity},
    )

    stock_event_id = emit_timeline_event(
        event="updated",
        log_group="stock",
        description=f"Stock updated for item #{item_id}",
        store_id=store_id,
        subject_type="Item",
        subject_id=item_id,
        payload={"item_id": item_id, "new_stock": new_stock},
        changes={"stock": {"from": old_stock, "to": new_stock}},
    )
    return {
        "id": order_id,
        "order_event_id": order_event_id,
        "stock_event_id": stock_event_id,
        "new_stock": new_stock,
    }


def update_stock(*, item_id: int, stock: int) -> dict:
    with _conn() as conn:
        item = conn.execute(
            "SELECT * FROM items WHERE id=?", (item_id,)
        ).fetchone()
        if not item:
            raise ValueError(f"item not found: {item_id}")

        old_stock = item["stock"]
        conn.execute(
            "UPDATE items SET stock=?, updated_at=? WHERE id=?",
            (stock, _now(), item_id),
        )
        conn.commit()

    event_id = emit_timeline_event(
        event="updated",
        log_group="stock",
        description=f"Manual stock update for item #{item_id}",
        store_id=item["store_id"],
        subject_type="Item",
        subject_id=item_id,
        payload={"item_id": item_id, "new_stock": stock},
        changes={"stock": {"from": old_stock, "to": stock}},
    )
    return {"item_id": item_id, "new_stock": stock, "event_id": event_id}


def update_product(
    *,
    item_id: int,
    name: Optional[str] = None,
    price: Optional[float] = None,
    stock: Optional[int] = None,
    category: Optional[str] = None,
) -> dict:
    updates: dict = {}
    changes: dict = {}
    with _conn() as conn:
        item = conn.execute(
            "SELECT * FROM items WHERE id=?", (item_id,)
        ).fetchone()
        if not item:
            raise ValueError(f"item not found: {item_id}")

        if name is not None:
            updates["name"] = name
            changes["name"] = {"from": item["name"], "to": name}
        if price is not None:
            updates["price"] = price
            changes["price"] = {"from": item["price"], "to": price}
        if stock is not None:
            updates["stock"] = stock
            changes["stock"] = {"from": item["stock"], "to": stock}
        if category is not None:
            updates["category"] = category

        ts = _now()
        for key, val in updates.items():
            conn.execute(
                f"UPDATE items SET {key}=?, updated_at=? WHERE id=?",
                (val, ts, item_id),
            )
        conn.commit()

    event_id = emit_timeline_event(
        event="updated",
        log_group="product",
        description=f"Ürün güncellendi: #{item_id}",
        store_id=item["store_id"],
        subject_type="Item",
        subject_id=item_id,
        payload={"item_id": item_id, **updates},
        changes=changes,
    )
    return {"item_id": item_id, "updates": updates, "event_id": event_id}


def update_discount(*, item_id: int, discount: float, store_id: Optional[int] = None) -> dict:
    with _conn() as conn:
        item = conn.execute(
            "SELECT * FROM items WHERE id=?", (item_id,)
        ).fetchone()
        if not item:
            raise ValueError(f"item not found: {item_id}")

        new_price = round(item["price"] * (1 - discount / 100), 2)
        conn.execute(
            "UPDATE items SET price=?, updated_at=? WHERE id=?",
            (new_price, _now(), item_id),
        )
        conn.commit()

    event_id = emit_timeline_event(
        event="updated",
        log_group="product",
        description=f"İndirim uygulandı: %{discount} — {item['name']}",
        store_id=store_id or item["store_id"],
        subject_type="Item",
        subject_id=item_id,
        payload={
            "item_id": item_id,
            "name": item["name"],
            "discount": discount,
            "discount_percent": discount,
            "price": new_price,
        },
        changes={
            "price": {"from": item["price"], "to": new_price},
            "discount": {"from": 0, "to": discount},
        },
    )
    return {"item_id": item_id, "discount": discount, "new_price": new_price, "event_id": event_id}


def create_review(
    *,
    store_id: int,
    item_id: Optional[int] = None,
    author: str = "Müşteri",
    rating: float = 5.0,
    comment: str = "",
    sentiment: Optional[str] = None,
) -> dict:
    if not sentiment:
        sentiment = "negative" if rating <= 2 else (
            "positive" if rating >= 4 else "neutral"
        )

    with _conn() as conn:
        cursor = conn.execute(
            """
            INSERT INTO reviews (
                store_id, item_id, author, rating, comment, sentiment, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (store_id, item_id, author, rating, comment, sentiment, _now()),
        )
        review_id = cursor.lastrowid
        conn.commit()

    event_id = emit_timeline_event(
        event="created" if rating >= 3 else "negative",
        log_group="review",
        description=f"Yorum: {rating}/5 — {comment[:60]}",
        store_id=store_id,
        subject_type="Review",
        subject_id=review_id,
        payload={
            "rating": rating,
            "comment": comment,
            "sentiment": sentiment,
            "item_id": item_id,
        },
    )
    return {"id": review_id, "event_id": event_id, "sentiment": sentiment}


def create_question(
    *,
    store_id: int,
    item_id: Optional[int] = None,
    author: str = "Müşteri",
    question: str,
) -> dict:
    with _conn() as conn:
        cursor = conn.execute(
            """
            INSERT INTO questions (
                store_id, item_id, author, question, created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (store_id, item_id, author, question, _now()),
        )
        q_id = cursor.lastrowid
        conn.commit()

    event_id = emit_timeline_event(
        event="created",
        log_group="customer",
        description=f"Müşteri sorusu: {question[:80]}",
        store_id=store_id,
        subject_type="Question",
        subject_id=q_id,
        payload={"question": question, "item_id": item_id},
    )
    return {"id": q_id, "event_id": event_id}


def shipping_delay(*, order_id: int, delay_days: int = 3, reason: str = "Kargo gecikmesi") -> dict:
    with _conn() as conn:
        order = conn.execute(
            "SELECT * FROM orders WHERE id=?", (order_id,)
        ).fetchone()
        if not order:
            raise ValueError(f"order not found: {order_id}")

        conn.execute(
            "UPDATE orders SET status=?, updated_at=? WHERE id=?",
            ("delayed", _now(), order_id),
        )
        conn.commit()

    event_id = emit_timeline_event(
        event="delayed",
        log_group="shipping",
        description=f"Sipariş #{order_id} gecikti: {reason}",
        store_id=order["store_id"],
        subject_type="Order",
        subject_id=order_id,
        payload={
            "delay_days": delay_days,
            "reason": reason,
            "shipping_delay": delay_days,
        },
    )
    return {"order_id": order_id, "event_id": event_id}


def update_banner_performance(
    *,
    banner_id: int,
    ctr: float = 0.05,
    impressions: int = 1000,
    clicks: int = 50,
) -> dict:
    with _conn() as conn:
        banner = conn.execute(
            "SELECT * FROM banners WHERE id=?", (banner_id,)
        ).fetchone()
        if not banner:
            raise ValueError(f"banner not found: {banner_id}")
        conn.execute(
            "UPDATE banners SET updated_at=? WHERE id=?",
            (_now(), banner_id),
        )
        conn.commit()

    event_id = emit_timeline_event(
        event="updated",
        log_group="banner",
        description=f"Banner performans güncellendi CTR={ctr:.2%}",
        store_id=banner["store_id"],
        subject_type="Banner",
        subject_id=banner_id,
        payload={
            "ctr": ctr,
            "click_rate": ctr,
            "impressions": impressions,
            "clicks": clicks,
        },
        changes={"ctr": {"from": 0, "to": ctr}},
    )
    return {"banner_id": banner_id, "event_id": event_id}


def update_sales(
    *,
    item_id: int,
    sales_change_pct: float = -20.0,
    sales: Optional[int] = None,
) -> dict:
    with _conn() as conn:
        item = conn.execute(
            "SELECT * FROM items WHERE id=?", (item_id,)
        ).fetchone()
        if not item:
            raise ValueError(f"item not found: {item_id}")

        new_sales = sales if sales is not None else max(
            0, int(item["sales"] * (1 + sales_change_pct / 100))
        )
        conn.execute(
            "UPDATE items SET sales=?, updated_at=? WHERE id=?",
            (new_sales, _now(), item_id),
        )
        conn.commit()

    event_id = emit_timeline_event(
        event="updated",
        log_group="sales",
        description=f"Satış güncellendi: %{sales_change_pct} değişim",
        store_id=item["store_id"],
        subject_type="Item",
        subject_id=item_id,
        payload={
            "sales_change_pct": sales_change_pct,
            "sales": new_sales,
            "item_id": item_id,
        },
        changes={"sales": {"from": item["sales"], "to": new_sales}},
    )
    return {"item_id": item_id, "sales": new_sales, "event_id": event_id}


def create_campaign(
    *,
    store_id: int,
    name: str,
    campaign_type: str = "promotion",
    discount_pct: float = 15.0,
) -> dict:
    ts = _now()
    with _conn() as conn:
        cursor = conn.execute(
            """
            INSERT INTO campaigns (
                store_id, name, campaign_type, discount_pct, status,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 'active', ?, ?)
            """,
            (store_id, name, campaign_type, discount_pct, ts, ts),
        )
        campaign_id = cursor.lastrowid
        conn.commit()

    event_id = emit_timeline_event(
        event="created",
        log_group="campaign",
        description=f"Kampanya başlatıldı: {name} (%{discount_pct})",
        store_id=store_id,
        subject_type="Campaign",
        subject_id=campaign_id,
        payload={
            "name": name,
            "discount_pct": discount_pct,
            "campaign_type": campaign_type,
        },
    )
    return {"id": campaign_id, "event_id": event_id}
