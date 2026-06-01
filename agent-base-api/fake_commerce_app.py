from env_bootstrap import load_app_env

load_app_env()

import json
import os
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Optional

from fastapi import (
    FastAPI,
    HTTPException,
    Depends,
    Header,
    Query,
)
from fastapi.responses import StreamingResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Tell internal_service.assert_not_internal_http_loop that we ARE the API
# process — any code path inside this process that tries to HTTP into
# 127.0.0.1 will now raise InternalHTTPLoopError instead of silently
# producing a self-referential request.
os.environ["INTERNAL_SERVICE_IN_PROCESS"] = "1"

import internal_service

# =========================================================
# APP
# =========================================================

app = FastAPI(
    title="Fake Sepetler AI API",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "*"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



API_PREFIX = "/api/ai/v1"

FAKE_TOKEN = "aio_test_token"

DB_PATH = "fake_ai_api.db"

# =========================================================
# DATABASE
# =========================================================

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def now_iso():
    return datetime.utcnow().isoformat()

def init_db():

    conn = get_db()
    c = conn.cursor()

    # =====================================================
    # STORES
    # =====================================================

    c.execute("""
        CREATE TABLE IF NOT EXISTS stores (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL,
            owner           TEXT,
            instagram       TEXT,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        )
    """)

    # =====================================================
    # ITEMS
    # =====================================================

    c.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id        INTEGER,
            name            TEXT NOT NULL,
            price           REAL,
            stock           INTEGER DEFAULT 0,
            sales           INTEGER DEFAULT 0,
            category        TEXT,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        )
    """)

    # =====================================================
    # ORDERS
    # =====================================================

    c.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id        INTEGER,
            item_id         INTEGER,
            quantity        INTEGER,
            status          TEXT DEFAULT 'pending',
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        )
    """)

    # =====================================================
    # BANNERS
    # =====================================================

    c.execute("""
        CREATE TABLE IF NOT EXISTS banners (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id        INTEGER,
            title           TEXT,
            image_url       TEXT,
            status          TEXT DEFAULT 'draft',
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        )
    """)

    # =====================================================
    # TIMELINE
    # =====================================================

    c.execute("""
        CREATE TABLE IF NOT EXISTS timeline (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            ts                  TEXT NOT NULL,

            event               TEXT NOT NULL,
            event_label         TEXT,

            log_group           TEXT NOT NULL,
            group_label         TEXT,

            description         TEXT,

            store_id            INTEGER,

            subject_type        TEXT,
            subject_id          INTEGER,

            causer_type         TEXT,
            causer_id           INTEGER,
            causer_name         TEXT,

            changes             TEXT,
            payload             TEXT,
            meta                TEXT
        )
    """)

    # =====================================================
    # AUTOMATION LOGS
    # =====================================================

    c.execute("""
        CREATE TABLE IF NOT EXISTS automation_logs (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,

            event_id            INTEGER,

            rule_name           TEXT,

            matched             INTEGER DEFAULT 1,

            ai_decision         TEXT,

            selected_tool       TEXT,

            tool_input          TEXT,

            tool_output         TEXT,

            execution_status    TEXT DEFAULT 'pending',

            retry_count         INTEGER DEFAULT 0,

            failed_reason       TEXT,

            latency_ms          INTEGER DEFAULT 0,

            created_at          TEXT NOT NULL,
            updated_at          TEXT NOT NULL
        )
    """)

    # =====================================================
    # SCHEDULED JOBS
    # =====================================================

    c.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_jobs (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,

            event_id            INTEGER,

            job_type            TEXT NOT NULL,

            tool_name           TEXT NOT NULL,

            payload             TEXT,

            run_at              TEXT NOT NULL,

            status              TEXT DEFAULT 'pending',

            retry_count         INTEGER DEFAULT 0,

            last_error          TEXT,

            locked_by           TEXT,
            locked_at           TEXT,

            executed_at         TEXT,

            created_at          TEXT NOT NULL
        )
    """)

    # =====================================================
    # LISTENER STATE
    # =====================================================

    c.execute("""
        CREATE TABLE IF NOT EXISTS listener_state (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,

            consumer_name       TEXT UNIQUE NOT NULL,

            last_cursor         INTEGER DEFAULT 1000,

            updated_at          TEXT NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS reviews (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id        INTEGER,
            item_id         INTEGER,
            author          TEXT,
            rating          REAL,
            comment         TEXT,
            sentiment       TEXT DEFAULT 'neutral',
            created_at      TEXT NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS questions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id        INTEGER,
            item_id         INTEGER,
            author          TEXT,
            question        TEXT,
            status          TEXT DEFAULT 'open',
            created_at      TEXT NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS campaigns (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id        INTEGER,
            name            TEXT NOT NULL,
            campaign_type   TEXT DEFAULT 'promotion',
            discount_pct    REAL DEFAULT 0,
            status          TEXT DEFAULT 'draft',
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()

init_db()

# =========================================================
# AUTH
# =========================================================

def verify_token(
    authorization: Optional[str] = Header(None)
):
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Missing authorization header"
        )

    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Invalid auth format"
        )

    token = authorization.replace("Bearer ", "")

    if token != FAKE_TOKEN:
        raise HTTPException(
            status_code=403,
            detail="Invalid token"
        )

    return {
        "token": token,
        "scope": "admin"
    }

# =========================================================
# MODELS
# =========================================================

class CreateStore(BaseModel):
    name: str
    owner: str
    instagram: Optional[str] = None
    logo_url: Optional[str] = None
    banner_url: Optional[str] = None
    theme_color: Optional[str] = None
    user_id: Optional[int] = None

class CreateProduct(BaseModel):
    store_id: int
    name: str
    price: float
    stock: int = 0
    category: Optional[str] = None
    image_url: Optional[str] = None
    images: Optional[list] = None
    discount_percent: Optional[float] = None
    user_id: Optional[int] = None

class CreateOrder(BaseModel):
    store_id: int
    item_id: int
    quantity: int

class CreateBanner(BaseModel):
    store_id: int
    title: str
    image_url: str

class UpdateProduct(BaseModel):
    item_id: int
    name: Optional[str] = None
    price: Optional[float] = None
    stock: Optional[int] = None
    category: Optional[str] = None

class UpdateDiscount(BaseModel):
    item_id: int
    discount: float
    store_id: Optional[int] = None

class CreateReview(BaseModel):
    store_id: int
    item_id: Optional[int] = None
    author: str = "Müşteri"
    rating: float = 5.0
    comment: str = ""
    sentiment: Optional[str] = None

class CreateQuestion(BaseModel):
    store_id: int
    item_id: Optional[int] = None
    author: str = "Müşteri"
    question: str

class ShippingDelay(BaseModel):
    order_id: int
    delay_days: int = 3
    reason: str = "Kargo gecikmesi"

class BannerPerformance(BaseModel):
    banner_id: int
    ctr: float = 0.05
    impressions: int = 1000
    clicks: int = 50

class UpdateSales(BaseModel):
    item_id: int
    sales_change_pct: float = -20.0
    sales: Optional[int] = None

class CreateCampaign(BaseModel):
    store_id: int
    name: str
    campaign_type: str = "promotion"
    discount_pct: float = 15.0

# =========================================================
# HELPERS
# =========================================================

def create_event(
    event: str,
    log_group: str,
    description: str,
    store_id: Optional[int] = None,
    subject_type: Optional[str] = None,
    subject_id: Optional[int] = None,
    payload: Optional[dict] = None,
    changes: Optional[dict] = None,
):

    conn = get_db()
    c = conn.cursor()

    group_labels = {
        "store": "Stores",
        "product": "Products",
        "order": "Orders",
        "stock": "Stocks",
        "banner": "Banners",
    }

    event_labels = {
        "created": "Created",
        "updated": "Updated",
        "deleted": "Deleted",
        "status_changed": "Status Changed",
    }

    c.execute("""
        INSERT INTO timeline (
            ts,

            event,
            event_label,

            log_group,
            group_label,

            description,

            store_id,

            subject_type,
            subject_id,

            causer_type,
            causer_id,
            causer_name,

            changes,
            payload,
            meta
        )
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        now_iso(),

        event,
        event_labels.get(event, event),

        log_group,
        group_labels.get(log_group, log_group),

        description,

        store_id,

        subject_type,
        subject_id,

        "system",
        1,
        "Fake API",

        json.dumps(changes or {}),
        json.dumps(payload or {}),
        json.dumps({
            "processed_by_rule_engine": False,
            "orchestration": {
                "path": "pending",
                "route": "unprocessed",
                "skip_reason": "listener bekleniyor",
            },
        }),
    ))

    event_id = c.lastrowid

    conn.commit()
    conn.close()

    return event_id

# =========================================================
# LISTENER STATE
# =========================================================

def get_listener_cursor(
    consumer_name: str = "default"
):
    conn = get_db()

    row = conn.execute("""
        SELECT * FROM listener_state
        WHERE consumer_name=?
    """, (consumer_name,)).fetchone()

    if not row:

        conn.execute("""
            INSERT INTO listener_state (
                consumer_name,
                last_cursor,
                updated_at
            )
            VALUES (?, ?, ?)
        """, (
            consumer_name,
            1000,
            now_iso(),
        ))

        conn.commit()
        conn.close()

        return 1000

    conn.close()

    return row["last_cursor"]

def set_listener_cursor(
    cursor: int,
    consumer_name: str = "default"
):
    conn = get_db()

    conn.execute("""
        UPDATE listener_state
        SET last_cursor=?, updated_at=?
        WHERE consumer_name=?
    """, (
        cursor,
        now_iso(),
        consumer_name,
    ))

    conn.commit()
    conn.close()

# =========================================================
# AUTOMATION LOGS
# =========================================================

def create_automation_log(
    event_id: int,
    rule_name: str,
    ai_decision: Optional[str] = None,
    selected_tool: Optional[str] = None,
    tool_input: Optional[dict] = None,
):

    conn = get_db()
    c = conn.cursor()

    ts = now_iso()

    c.execute("""
        INSERT INTO automation_logs (
            event_id,
            rule_name,
            ai_decision,
            selected_tool,
            tool_input,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        event_id,
        rule_name,
        ai_decision,
        selected_tool,
        json.dumps(tool_input or {}),
        ts,
        ts,
    ))

    log_id = c.lastrowid

    conn.commit()
    conn.close()

    return log_id

def finish_automation_log(
    log_id: int,
    status: str = "success",
    tool_output: Optional[dict] = None,
    failed_reason: Optional[str] = None,
    latency_ms: int = 0,
):

    conn = get_db()

    conn.execute("""
        UPDATE automation_logs
        SET
            execution_status=?,
            tool_output=?,
            failed_reason=?,
            latency_ms=?,
            updated_at=?
        WHERE id=?
    """, (
        status,
        json.dumps(tool_output or {}),
        failed_reason,
        latency_ms,
        now_iso(),
        log_id,
    ))

    conn.commit()
    conn.close()

# =========================================================
# SCHEDULED JOBS
# =========================================================

def schedule_job(
    event_id: int,
    tool_name: str,
    payload: dict,
    delay_minutes: int = 0,
    job_type: str = "automation",
):

    conn = get_db()
    c = conn.cursor()

    run_at = (
        datetime.utcnow() +
        timedelta(minutes=delay_minutes)
    ).isoformat()

    c.execute("""
        INSERT INTO scheduled_jobs (
            event_id,
            job_type,
            tool_name,
            payload,
            run_at,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        event_id,
        job_type,
        tool_name,
        json.dumps(payload or {}),
        run_at,
        now_iso(),
    ))

    job_id = c.lastrowid

    conn.commit()
    conn.close()

    return job_id

# =========================================================
# HEALTH
# =========================================================

@app.get(f"{API_PREFIX}/health")
async def health(
    auth=Depends(verify_token)
):
    return {
        "data": {
            "ok": True,
            "server_time": now_iso(),
            "agent": {
                "id": 1,
                "name": "Fake AI Agent",
                "status": "active"
            },
            "token": {
                "scope": auth["scope"],
                "rate_limit_per_min": 999
            }
        }
    }

# =========================================================
# TIMELINE
# =========================================================

@app.get(f"{API_PREFIX}/timeline")
async def timeline(
    limit: int = 50,
    cursor: int = 0,
    direction: str = "desc",

    log_group: Optional[str] = None,
    event: Optional[str] = None,
    store_id: Optional[int] = None,

    auth=Depends(verify_token)
):

    conn = get_db()

    query = "SELECT * FROM timeline WHERE 1=1"
    params = []

    if cursor:
        if direction == "asc":
            query += " AND id > ?"
        else:
            query += " AND id < ?"

        params.append(cursor)

    if log_group:
        query += " AND log_group=?"
        params.append(log_group)

    if event:
        query += " AND event=?"
        params.append(event)

    if store_id:
        query += " AND store_id=?"
        params.append(store_id)

    order = "ASC" if direction == "asc" else "DESC"

    query += f" ORDER BY id {order} LIMIT ?"

    params.append(limit)

    rows = conn.execute(query, params).fetchall()

    events = []

    for row in rows:
        events.append({
            "id": row["id"],
            "ts": row["ts"],

            "event": row["event"],
            "event_label": row["event_label"],

            "group": row["log_group"],
            "group_label": row["group_label"],

            "description": row["description"],

            "store_id": row["store_id"],

            "subject": {
                "type": row["subject_type"],
                "id": row["subject_id"],
            },

            "causer": {
                "type": row["causer_type"],
                "id": row["causer_id"],
                "name": row["causer_name"],
            },

            "changes": json.loads(row["changes"] or "{}"),
            "payload": json.loads(row["payload"] or "{}"),
            "meta": json.loads(row["meta"] or "{}"),
        })

    conn.close()

    next_cursor = (
        events[-1]["id"]
        if events else None
    )

    return {
        "data": events,
        "pagination": {
            "count": len(events),
            "limit": limit,
            "direction": direction,
            "next_cursor": next_cursor,
            "has_more": len(events) >= limit,
        }
    }

# =========================================================
# SUBJECT TIMELINE
# =========================================================

@app.get(f"{API_PREFIX}/subjects/{{subject_type}}/{{subject_id}}/timeline")
async def subject_timeline(
    subject_type: str,
    subject_id: int,
    auth=Depends(verify_token)
):

    conn = get_db()

    rows = conn.execute("""
        SELECT * FROM timeline
        WHERE subject_type=?
        AND subject_id=?
        ORDER BY id DESC
    """, (
        subject_type,
        subject_id,
    )).fetchall()

    conn.close()

    data = []

    for row in rows:
        data.append({
            "id": row["id"],
            "event": row["event"],
            "group": row["log_group"],
            "description": row["description"],
            "changes": json.loads(row["changes"] or "{}"),
            "ts": row["ts"],
        })

    return {
        "data": data
    }

# =========================================================
# RESOURCES DISCOVERY
# =========================================================

@app.get(f"{API_PREFIX}/resources")
async def resources(
    auth=Depends(verify_token)
):

    return {
        "data": [
            {
                "type": "stores",
                "filters": [
                    "id",
                    "name",
                ],
                "sortable": [
                    "id",
                    "created_at",
                ],
                "includes": [],
                "accessible": True,
            },
            {
                "type": "items",
                "filters": [
                    "store_id",
                    "name",
                    "category",
                ],
                "sortable": [
                    "id",
                    "price",
                    "sales",
                    "stock",
                ],
                "includes": [
                    "store",
                ],
                "accessible": True,
            },
            {
                "type": "orders",
                "filters": [
                    "store_id",
                    "status",
                ],
                "sortable": [
                    "id",
                    "created_at",
                ],
                "includes": [
                    "item",
                ],
                "accessible": True,
            },
        ]
    }

# =========================================================
# STORES
# =========================================================

@app.get(f"{API_PREFIX}/resources/stores")
async def get_stores(
    search: Optional[str] = None,
    sort: Optional[str] = None,
    fields: Optional[str] = None,
    auth=Depends(verify_token)
):

    conn = get_db()

    query = "SELECT * FROM stores WHERE 1=1"
    params = []

    if search:
        query += " AND name LIKE ?"
        params.append(f"%{search}%")

    if sort:
        direction = "ASC"

        if sort.startswith("-"):
            direction = "DESC"
            sort = sort[1:]

        query += f" ORDER BY {sort} {direction}"

    rows = conn.execute(query, params).fetchall()

    conn.close()

    allowed_fields = (
        fields.split(",")
        if fields else None
    )

    data = []

    for row in rows:

        item = dict(row)

        if allowed_fields:
            item = {
                k: v for k, v in item.items()
                if k in allowed_fields
            }

        data.append(item)

    return {
        "data": data
    }

@app.get(f"{API_PREFIX}/resources/stores/{{store_id}}")
async def get_store_by_id(
    store_id: int,
    auth=Depends(verify_token),
):
    conn = get_db()

    row = conn.execute(
        "SELECT * FROM stores WHERE id=?",
        (store_id,),
    ).fetchone()

    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Store not found")

    return {"data": dict(row)}

# =========================================================
# ITEMS
# =========================================================

@app.get(f"{API_PREFIX}/resources/items")
async def get_items(
    store_id: Optional[int] = None,
    search: Optional[str] = None,
    sort: Optional[str] = None,
    include: Optional[str] = None,
    fields: Optional[str] = None,
    auth=Depends(verify_token)
):

    conn = get_db()

    query = "SELECT * FROM items WHERE 1=1"
    params = []

    if store_id:
        query += " AND store_id=?"
        params.append(store_id)

    if search:
        query += " AND name LIKE ?"
        params.append(f"%{search}%")

    if sort:

        direction = "ASC"

        if sort.startswith("-"):
            direction = "DESC"
            sort = sort[1:]

        query += f" ORDER BY {sort} {direction}"

    rows = conn.execute(query, params).fetchall()

    allowed_fields = (
        fields.split(",")
        if fields else None
    )

    data = []

    for row in rows:

        item = dict(row)

        if allowed_fields:
            item = {
                k: v for k, v in item.items()
                if k in allowed_fields
            }

        if include == "store":

            store = conn.execute("""
                SELECT * FROM stores
                WHERE id=?
            """, (
                row["store_id"],
            )).fetchone()

            item["store"] = (
                dict(store)
                if store else None
            )

        data.append(item)

    conn.close()

    return {
        "data": data
    }

@app.get(f"{API_PREFIX}/resources/items/{{item_id}}")
async def get_item_by_id(
    item_id: int,
    include: Optional[str] = None,
    auth=Depends(verify_token),
):
    conn = get_db()

    row = conn.execute(
        "SELECT * FROM items WHERE id=?",
        (item_id,),
    ).fetchone()

    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Item not found")

    item = dict(row)

    if include == "store":
        store = conn.execute(
            "SELECT * FROM stores WHERE id=?",
            (row["store_id"],),
        ).fetchone()
        item["store"] = dict(store) if store else None

    conn.close()

    return {"data": item}

# =========================================================
# ORDERS
# =========================================================

@app.get(f"{API_PREFIX}/resources/orders")
async def get_orders(
    auth=Depends(verify_token)
):

    conn = get_db()

    rows = conn.execute("""
        SELECT * FROM orders
        ORDER BY id DESC
    """).fetchall()

    conn.close()

    return {
        "data": [
            dict(r)
            for r in rows
        ]
    }

@app.get(f"{API_PREFIX}/resources/orders/{{order_id}}")
async def get_order_by_id(
    order_id: int,
    auth=Depends(verify_token),
):
    conn = get_db()

    row = conn.execute(
        "SELECT * FROM orders WHERE id=?",
        (order_id,),
    ).fetchone()

    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Order not found")

    return {"data": dict(row)}

# =========================================================
# BANNERS
# =========================================================

@app.get(f"{API_PREFIX}/banners")
async def get_banners(
    auth=Depends(verify_token)
):

    conn = get_db()

    rows = conn.execute("""
        SELECT * FROM banners
        ORDER BY id DESC
    """).fetchall()

    conn.close()

    return {
        "data": [
            dict(r)
            for r in rows
        ]
    }

@app.post(f"{API_PREFIX}/banners")
async def create_banner(
    data: CreateBanner,
    auth=Depends(verify_token)
):

    conn = get_db()
    c = conn.cursor()

    ts = now_iso()

    c.execute("""
        INSERT INTO banners (
            store_id,
            title,
            image_url,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?)
    """, (
        data.store_id,
        data.title,
        data.image_url,
        ts,
        ts,
    ))

    banner_id = c.lastrowid

    conn.commit()
    conn.close()

    create_event(
        event="created",
        log_group="banner",
        description=f"Banner created: {data.title}",
        store_id=data.store_id,
        subject_type="Banner",
        subject_id=banner_id,
        payload=data.dict(),
    )

    return {
        "data": {
            "id": banner_id
        }
    }

# =========================================================
# INSIGHTS
# =========================================================

@app.get(f"{API_PREFIX}/insights/products/top")
async def top_products(
    auth=Depends(verify_token)
):

    conn = get_db()

    rows = conn.execute("""
        SELECT * FROM items
        ORDER BY sales DESC
        LIMIT 10
    """).fetchall()

    conn.close()

    return {
        "data": {
            "items": [
                dict(r)
                for r in rows
            ]
        }
    }

@app.get(f"{API_PREFIX}/insights/inventory")
async def inventory(
    auth=Depends(verify_token)
):

    conn = get_db()

    rows = conn.execute("""
        SELECT * FROM items
    """).fetchall()

    conn.close()

    low_stock = []

    for row in rows:
        if row["stock"] < 10:
            low_stock.append(dict(row))

    return {
        "data": {
            "summary": {
                "total_items": len(rows),
                "low_stock_count": len(low_stock),
            },
            "low_stock_items": low_stock,
        }
    }

# =========================================================
# STREAM
# =========================================================

@app.get(f"{API_PREFIX}/timeline/stream")
async def timeline_stream(
    auth=Depends(verify_token)
):

    def generate():

        conn = get_db()

        rows = conn.execute("""
            SELECT * FROM timeline
            ORDER BY id ASC
        """).fetchall()

        conn.close()

        for row in rows:

            payload = {
                "id": row["id"],
                "event": row["event"],
                "group": row["log_group"],
                "description": row["description"],
                "ts": row["ts"],
            }

            yield (
                json.dumps(payload)
                + "\n"
            )

            time.sleep(0.1)

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson"
    )

# =========================================================
# READ — STORES / ITEMS  (UI Mağazalar sayfası için)
# =========================================================

@app.get("/stores")
def list_stores_endpoint(user_id: int = 1):
    """user_id'nin mağazaları — birincil sinyal `fake_ai_api.db.timeline.causer_id`.

    fake_ai_api.db.stores tablosunda user_id kolonu yok; ama her store oluşumu
    timeline'a `causer_id` ile yazılır. Bu join doğru kullanıcı→mağaza haritası
    verir. listener.db'den de user_id=? satırları eklenir (sync olunca isim
    çakışmıyorsa) — geri uyumluluk için.
    """
    import sqlite3 as _sqlite3
    out: list[dict] = []
    seen_names: set[str] = set()

    # 1) fake_ai_api.db: timeline.causer_id JOIN stores  → user-eklediği gerçek mağazalar
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT s.*
            FROM stores s
            JOIN timeline t ON t.store_id = s.id
            WHERE t.log_group = 'store'
              AND t.event = 'created'
              AND t.causer_id = ?
            ORDER BY s.id DESC
            """,
            (user_id,),
        ).fetchall()
        for r in rows:
            d = dict(r)
            out.append(d)
            n = str(d.get("name") or "").strip()
            if n:
                seen_names.add(n)
    except Exception:
        # timeline yoksa veya schema farklıysa sessizce geç
        pass
    finally:
        conn.close()

    # 2) listener.db: sync edilmiş ek mağazalar (varsa) — isim çakışmıyorsa ekle
    try:
        lconn = _sqlite3.connect("listener.db", timeout=2)
        lconn.row_factory = _sqlite3.Row
        try:
            lrows = lconn.execute(
                "SELECT * FROM stores WHERE user_id=? ORDER BY id DESC",
                (user_id,),
            ).fetchall()
            for r in lrows:
                d = dict(r)
                n = str(d.get("name") or "").strip()
                if n and n in seen_names:
                    continue
                out.append(d)
                if n:
                    seen_names.add(n)
        finally:
            lconn.close()
    except Exception:
        # listener.db yoksa veya schema farklıysa sessizce geç
        pass

    return {"data": out}


@app.get("/stores/{store_id}/items")
def list_store_items_endpoint(store_id: int):
    """Bir mağazanın ürünlerini listele. SELECT * — kolon farklılıklarına
    karşı UI null-coalesce yapsın."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM items WHERE store_id=? ORDER BY id DESC",
            (store_id,),
        ).fetchall()
        return {"data": [dict(r) for r in rows]}
    finally:
        conn.close()


# =========================================================
# INTERNAL — CREATE STORE
# =========================================================

@app.post("/internal/create-store")
async def internal_create_store(data: CreateStore, user_id: int = 1):
    """`user_id` → timeline event'in causer_id'si. Default 1 (legacy).
    Body'de `user_id` field'ı geçirilirse o öncelikli; aksi halde query."""
    body_uid = getattr(data, "user_id", None)
    effective_uid = body_uid if isinstance(body_uid, int) else user_id
    result = internal_service.create_store(
        name=data.name, owner=data.owner, instagram=data.instagram,
        logo_url=data.logo_url, banner_url=data.banner_url,
        theme_color=data.theme_color,
        user_id=effective_uid,
    )
    return {"data": {"id": result["id"]}}

# =========================================================
# INTERNAL — CREATE PRODUCT
# =========================================================

@app.post("/internal/create-product")
async def internal_create_product(data: CreateProduct, user_id: int = 1):
    """`user_id` query parametresi → timeline event'in causer_id'si.
    Default 1 (legacy). Body'de `user_id` field'ı geçirilirse o öncelikli."""
    # Body field override (operatörler body içinde user_id yazıyor olabilir)
    body_uid = getattr(data, "user_id", None)
    effective_uid = body_uid if isinstance(body_uid, int) else user_id
    result = internal_service.create_product(
        store_id=data.store_id,
        name=data.name,
        price=data.price,
        stock=data.stock,
        category=data.category,
        image_url=data.image_url,
        images=data.images,
        discount_percent=data.discount_percent,
        user_id=effective_uid,
    )
    return {"data": {"id": result["id"]}}

# =========================================================
# INTERNAL — CREATE ORDER
# =========================================================

@app.post("/internal/create-order")
async def internal_create_order(data: CreateOrder):
    try:
        result = internal_service.create_order(
            store_id=data.store_id,
            item_id=data.item_id,
            quantity=data.quantity,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc))

    # Low-stock automation log + scheduled-job side effect — preserved
    # behavior, but the timeline + state mutation lives in the service.
    log_id = create_automation_log(
        event_id=result["stock_event_id"],
        rule_name="low_stock_check",
        ai_decision="Check if stock < 10",
        selected_tool="notification_tool",
        tool_input={
            "item_id": data.item_id,
            "stock": result["new_stock"],
        },
    )
    if result["new_stock"] < 10:
        schedule_job(
            event_id=result["stock_event_id"],
            tool_name="send_low_stock_notification",
            payload={"item_id": data.item_id, "stock": result["new_stock"]},
            delay_minutes=0,
        )
        finish_automation_log(log_id, status="success", tool_output={"scheduled": True})
    else:
        finish_automation_log(log_id, status="success", tool_output={"scheduled": False})

    return {
        "data": {
            "id": result["id"],
            "order_event_id": result["order_event_id"],
            "stock_event_id": result["stock_event_id"],
        }
    }

# =========================================================
# INTERNAL — UPDATE STOCK
# =========================================================

@app.post("/internal/update-stock")
async def internal_update_stock(item_id: int, stock: int):
    try:
        internal_service.update_stock(item_id=item_id, stock=stock)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return {"success": True}


# =========================================================
# INTERNAL — UPDATE PRODUCT
# =========================================================

@app.post("/internal/update-product")
async def internal_update_product(data: UpdateProduct):
    try:
        internal_service.update_product(
            item_id=data.item_id,
            name=data.name,
            price=data.price,
            stock=data.stock,
            category=data.category,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return {"success": True, "item_id": data.item_id}


# =========================================================
# INTERNAL — UPDATE DISCOUNT
# =========================================================

@app.post("/internal/update-discount")
async def internal_update_discount(data: UpdateDiscount):
    try:
        result = internal_service.update_discount(
            item_id=data.item_id,
            discount=data.discount,
            store_id=data.store_id,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return {"success": True, "discount": data.discount, "new_price": result["new_price"]}


# =========================================================
# INTERNAL — CREATE REVIEW
# =========================================================

@app.post("/internal/create-review")
async def internal_create_review(data: CreateReview):
    result = internal_service.create_review(
        store_id=data.store_id,
        item_id=data.item_id,
        author=data.author,
        rating=data.rating,
        comment=data.comment,
        sentiment=data.sentiment,
    )
    return {"data": {"id": result["id"]}}


# =========================================================
# INTERNAL — CREATE QUESTION
# =========================================================

@app.post("/internal/create-question")
async def internal_create_question(data: CreateQuestion):
    result = internal_service.create_question(
        store_id=data.store_id,
        item_id=data.item_id,
        author=data.author,
        question=data.question,
    )
    return {"data": {"id": result["id"]}}


# =========================================================
# INTERNAL — SHIPPING DELAY
# =========================================================

@app.post("/internal/shipping-delay")
async def internal_shipping_delay(data: ShippingDelay):
    try:
        internal_service.shipping_delay(
            order_id=data.order_id,
            delay_days=data.delay_days,
            reason=data.reason,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return {"success": True, "order_id": data.order_id}


# =========================================================
# INTERNAL — BANNER PERFORMANCE
# =========================================================

@app.post("/internal/update-banner-performance")
async def internal_banner_performance(data: BannerPerformance):
    try:
        internal_service.update_banner_performance(
            banner_id=data.banner_id,
            ctr=data.ctr,
            impressions=data.impressions,
            clicks=data.clicks,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return {"success": True}


# =========================================================
# INTERNAL — UPDATE SALES
# =========================================================

@app.post("/internal/update-sales")
async def internal_update_sales(data: UpdateSales):
    try:
        result = internal_service.update_sales(
            item_id=data.item_id,
            sales_change_pct=data.sales_change_pct,
            sales=data.sales,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return {"success": True, "sales": result["sales"]}


# =========================================================
# INTERNAL — CREATE CAMPAIGN
# =========================================================

@app.post("/internal/create-campaign")
async def internal_create_campaign(data: CreateCampaign):
    result = internal_service.create_campaign(
        store_id=data.store_id,
        name=data.name,
        campaign_type=data.campaign_type,
        discount_pct=data.discount_pct,
    )
    return {"data": {"id": result["id"]}}


# =========================================================
# REPLAY
# =========================================================

@app.get(f"{API_PREFIX}/replay")
async def replay_events(
    from_cursor: int,
    to_cursor: int,
    auth=Depends(verify_token)
):

    conn = get_db()

    rows = conn.execute("""
        SELECT * FROM timeline
        WHERE id >= ?
        AND id <= ?
        ORDER BY id ASC
    """, (
        from_cursor,
        to_cursor,
    )).fetchall()

    conn.close()

    return {
        "data": [
            {
                "id": r["id"],
                "event": r["event"],
                "group": r["log_group"],
                "description": r["description"],
            }
            for r in rows
        ]
    }

# =========================================================
# ORCHESTRATION INTERNAL API + DASHBOARD
# =========================================================

from orchestration_api import router as orchestration_router

app.include_router(orchestration_router)


@app.get("/")
async def root_redirect():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/api/internal/dashboard")


# =========================================================
# RUN
# =========================================================

"""
INSTALL:

pip install fastapi uvicorn

RUN:

uvicorn main:app --reload


TOKEN:

aio_test_token


EXAMPLES:

curl http://127.0.0.1:8000/api/ai/v1/health \
-H "Authorization: Bearer aio_test_token"
export TOKEN="aio_test_token"


curl -X POST http://127.0.0.1:8000/internal/create-store \
-H "Content-Type: application/json" \
-d '{
  "name": "Coffee Lab",
  "owner": "Oktay"
}'


curl -X POST http://127.0.0.1:8000/internal/create-product \
-H "Content-Type: application/json" \
-d '{
  "store_id": 1,
  "name": "Premium Mug",
  "price": 299,
  "stock": 50
}'


curl -X POST http://127.0.0.1:8000/internal/create-order \
-H "Content-Type: application/json" \
-d '{
  "store_id": 1,
  "item_id": 1,
  "quantity": 2
}'


curl "http://127.0.0.1:8000/api/ai/v1/timeline?direction=asc&cursor=0" \
-H "Authorization: Bearer aio_test_token"


curl "http://127.0.0.1:8000/api/ai/v1/resources/items?include=store&sort=-sales" \
-H "Authorization: Bearer aio_test_token"


curl "http://127.0.0.1:8000/api/ai/v1/subjects/Item/1/timeline" \
-H "Authorization: Bearer aio_test_token"


curl "http://127.0.0.1:8000/api/ai/v1/replay?from_cursor=1&to_cursor=10" \
-H "Authorization: Bearer aio_test_token"
"""