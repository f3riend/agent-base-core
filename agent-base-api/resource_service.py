"""Resource sync from Fake API and local DB reads."""

import os
import time

import requests

from db import get_db, now_iso

API_URL = os.environ.get("API_URL", "http://127.0.0.1:8000/api/ai/v1")
TOKEN = os.environ.get("API_TOKEN", "aio_test_token")

HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def _api_get(path, params=None):
    r = requests.get(
        f"{API_URL}{path}",
        headers=HEADERS,
        params=params or {},
        timeout=30,
    )
    if r.status_code == 404:
        return None
    r.raise_for_status()
    body = r.json()
    return body.get("data")


def fetch_store(store_id: int):
    return _api_get(f"/resources/stores/{store_id}")


def fetch_item(item_id: int, include_store: bool = True):
    params = {"include": "store"} if include_store else None
    return _api_get(f"/resources/items/{item_id}", params=params)


def fetch_order(order_id: int):
    return _api_get(f"/resources/orders/{order_id}")


def fetch_events(cursor: int):
    """Listener polling — direct DB via timeline_service (no HTTP)."""
    from timeline_service import fetch_events_after_cursor

    return fetch_events_after_cursor(cursor=cursor, limit=100)


def upsert_store(store, status: str | None = None, user_id: int = 1):
    conn = get_db()
    store_status = status or store.get("status", "active")
    conn.execute(
        """
        INSERT OR REPLACE INTO stores (
            id, user_id, name, owner, instagram, status, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            store["id"],
            user_id,
            store["name"],
            store["owner"],
            store.get("instagram"),
            store_status,
            store["created_at"],
            store["updated_at"],
        ),
    )
    conn.commit()
    conn.close()
    print(f"[SYNC] Store synced #{store['id']} (status={store_status})")


def upsert_item(item, status: str | None = None, user_id: int = 1):
    conn = get_db()
    item_status = status or item.get("status", "active")
    conn.execute(
        """
        INSERT OR REPLACE INTO items (
            id, store_id, user_id, name, price, stock, sales,
            category, status, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            item["id"],
            item["store_id"],
            user_id,
            item["name"],
            item["price"],
            item["stock"],
            item["sales"],
            item.get("category"),
            item_status,
            item["created_at"],
            item["updated_at"],
        ),
    )
    conn.commit()
    conn.close()
    print(f"[SYNC] Item synced #{item['id']} (status={item_status})")


def upsert_order(order):
    conn = get_db()
    conn.execute(
        """
        INSERT OR REPLACE INTO orders (
            id, store_id, item_id, quantity, status, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            order["id"],
            order["store_id"],
            order["item_id"],
            order["quantity"],
            order["status"],
            order["created_at"],
            order["updated_at"],
        ),
    )
    conn.commit()
    conn.close()
    print(f"[SYNC] Order synced #{order['id']}")


def mark_store_status(store_id: int, status: str):
    conn = get_db()
    conn.execute(
        "UPDATE stores SET status=?, updated_at=? WHERE id=?",
        (status, now_iso(), store_id),
    )
    conn.commit()
    conn.close()


def mark_item_status(item_id: int, status: str):
    conn = get_db()
    conn.execute(
        "UPDATE items SET status=?, updated_at=? WHERE id=?",
        (status, now_iso(), item_id),
    )
    conn.commit()
    conn.close()


def get_store(store_id: int):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM stores WHERE id=?", (store_id,)
    ).fetchone()
    conn.close()
    return row


def get_item(item_id: int):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM items WHERE id=?", (item_id,)
    ).fetchone()
    conn.close()
    return row


def _fetch_row(table: str, row_id: int) -> dict | None:
    """Generic DB row fetch — tablo yoksa veya kayıt yoksa None."""
    try:
        conn = get_db()
        row = conn.execute(
            f"SELECT * FROM {table} WHERE id=?", (int(row_id),)
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception:
        # Tablo yok (sqlite OperationalError) veya başka hata — graceful
        return None


def fetch_review(review_id: int) -> dict | None:
    """Review row fetch (fake_ai_api.db.reviews)."""
    return _fetch_row("reviews", review_id)


def fetch_campaign(campaign_id: int) -> dict | None:
    return _fetch_row("campaigns", campaign_id)


def fetch_banner(banner_id: int) -> dict | None:
    return _fetch_row("banners", banner_id)


def fetch_story(story_id: int) -> dict | None:
    """Story row fetch. fake_ai_api.db'de henüz `stories` tablosu yok →
    None döner ve listener bunu event payload'ından yedek olarak okur.
    """
    return _fetch_row("stories", story_id)


def fetch_coupon(coupon_id: int) -> dict | None:
    """Coupon row fetch. `coupons` tablosu henüz yok → None."""
    return _fetch_row("coupons", coupon_id)


def _fake_db_conn():
    """fake_ai_api.db (mock e-ticaret) için doğrudan SQLite bağlantısı.
    `db.get_db()` listener.db'ye bağlanır; ürün/mağaza görsel verileri
    fake_ai_api.db'deki items/stores tablolarında olduğu için ayrı conn."""
    import os as _os, sqlite3 as _sqlite3
    path = _os.environ.get("FAKE_API_DB_PATH", "fake_ai_api.db")
    conn = _sqlite3.connect(path)
    conn.row_factory = _sqlite3.Row
    return conn


def fetch_item_images(item_id: int) -> list[str]:
    """Item'ın tüm görsel URL'lerini döner — fake_ai_api.db.items.

    items.image_url (singular) + items.images_json (JSON array) birleştirir.
    Tablo/kolon/satır yoksa boş list.
    """
    try:
        import json as _json
        conn = _fake_db_conn()
        row = conn.execute(
            "SELECT image_url, images_json FROM items WHERE id=?",
            (int(item_id),),
        ).fetchone()
        conn.close()
        if not row:
            return []
        urls: list[str] = []
        image_url = row["image_url"] if "image_url" in row.keys() else None
        if image_url:
            urls.append(str(image_url))
        images_json = row["images_json"] if "images_json" in row.keys() else None
        if images_json:
            try:
                extra = _json.loads(images_json)
                if isinstance(extra, list):
                    urls.extend([str(u) for u in extra if u])
            except (ValueError, TypeError):
                pass
        seen, out = set(), []
        for u in urls:
            u = (u or "").strip()
            if u and u not in seen:
                seen.add(u); out.append(u)
        return out
    except Exception:
        return []


def fetch_store_logo(store_id: int) -> str | None:
    """Mağaza logo URL'i — fake_ai_api.db.stores.logo_url. Yoksa None."""
    try:
        conn = _fake_db_conn()
        row = conn.execute(
            "SELECT logo_url FROM stores WHERE id=?", (int(store_id),),
        ).fetchone()
        conn.close()
        if not row:
            return None
        url = row["logo_url"] if "logo_url" in row.keys() else None
        return str(url).strip() if url else None
    except Exception:
        return None


def fetch_store_banner(store_id: int) -> str | None:
    """Mağaza banner URL'i — fake_ai_api.db.stores.banner_url. Yoksa None."""
    try:
        conn = _fake_db_conn()
        row = conn.execute(
            "SELECT banner_url FROM stores WHERE id=?", (int(store_id),),
        ).fetchone()
        conn.close()
        if not row:
            return None
        url = row["banner_url"] if "banner_url" in row.keys() else None
        return str(url).strip() if url else None
    except Exception:
        return None


def build_rule_context(subject_type: str, subject_id: int, event: dict):
    """Build evaluation context with defaults for rule engine.

    Yeni subject_type'lar (Review, Campaign, Banner, Story, Coupon) için
    DB'den fetch dener; bulunamazsa event payload'ından minimal context
    kurar — böylece kural koşulları (`category`, `price`, ...) yine de
    çalışır.
    """
    context = {}

    if subject_type == "Store":
        store = fetch_store(subject_id)
        if store:
            store = dict(store)
            store.setdefault("active", True)
            store.setdefault("status", "active")
            # Logo + banner — varsa direkt DB'den çek
            store_logo = fetch_store_logo(subject_id)
            if store_logo:
                store["logo_url"] = store_logo
            store_banner = fetch_store_banner(subject_id)
            if store_banner:
                store["banner_url"] = store_banner
            context["store"] = store

    elif subject_type == "Item":
        # fetch_item HTTP üzerinden — başarısız olabilir; en azından minimal item
        item = fetch_item(subject_id) or {"id": int(subject_id)}
        item = dict(item)
        item.setdefault("status", "active")

        # Ürün görselleri — fetch_item başarısından bağımsız, doğrudan SQLite
        item_images = fetch_item_images(subject_id)
        if item_images:
            item["image_urls"] = item_images
            item["primary_image_url"] = item_images[0]
            item.setdefault("image_url", item_images[0])

        # Mağaza logosu — item.store_id veya event.payload.store_id'den dene
        candidate_store_id = (
            item.get("store_id")
            or (event.get("payload") or {}).get("store_id")
        )
        if candidate_store_id:
            try:
                sid = int(candidate_store_id)
                logo = fetch_store_logo(sid)
                if logo:
                    item["store_logo_url"] = logo
                banner = fetch_store_banner(sid)
                if banner:
                    item["store_banner_url"] = banner
            except (ValueError, TypeError):
                pass

        context["item"] = item

        changes = event.get("changes") or {}
        stock_change = changes.get("stock")
        if stock_change and "to" in stock_change:
            context["item"]["stock"] = stock_change["to"]

        payload = event.get("payload") or {}
        if "new_stock" in payload:
            context["item"]["stock"] = payload["new_stock"]

    elif subject_type == "Order":
        order = fetch_order(subject_id)
        if order:
            context["order"] = dict(order)

    elif subject_type == "Review":
        row = fetch_review(subject_id) or {}
        # event payload'tan da merge — DB'de yoksa context yine de dolsun
        payload = event.get("payload") or {}
        merged = {**payload, **row}
        merged.setdefault("id", subject_id)
        context["review"] = merged

    elif subject_type == "Campaign":
        row = fetch_campaign(subject_id) or {}
        payload = event.get("payload") or {}
        merged = {**payload, **row}
        merged.setdefault("id", subject_id)
        context["campaign"] = merged

    elif subject_type == "Banner":
        row = fetch_banner(subject_id) or {}
        payload = event.get("payload") or {}
        merged = {**payload, **row}
        merged.setdefault("id", subject_id)
        context["banner"] = merged

    elif subject_type == "Story":
        row = fetch_story(subject_id) or {}
        payload = event.get("payload") or {}
        merged = {**payload, **row}
        merged.setdefault("id", subject_id)
        context["story"] = merged

    elif subject_type == "Coupon":
        row = fetch_coupon(subject_id) or {}
        payload = event.get("payload") or {}
        merged = {**payload, **row}
        merged.setdefault("id", subject_id)
        context["coupon"] = merged

    return context
