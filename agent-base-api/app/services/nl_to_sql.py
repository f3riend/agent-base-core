"""nl_to_sql.py — Doğal dil sorusunu PostgreSQL sorgusuna çevirir.

LLM şemayı okur, sorguyu yazar, sistem çalıştırır.
Tamamen dinamik — intent tanımı yok, şablon yok.

GÜVENLİK:
  - Sadece SELECT
  - INSERT/UPDATE/DELETE/DROP/TRUNCATE/ALTER yasak
  - Tüm parametreler bind variable — SQL injection yok
  - store_ids her zaman WHERE'de — tenant izolasyonu
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

from sqlalchemy import text


_SCHEMA = """
KULLANILACAK TABLOLAR (sadece bunlar — başka tablo yok):

stores          → id UUID, user_id INT, name TEXT, rating NUMERIC, status TEXT
products        → id UUID, store_id UUID, name TEXT, brand TEXT, category TEXT,
                  price NUMERIC, cost_price NUMERIC, discount NUMERIC,
                  stock_quantity INT, stock_alert_level INT, is_active BOOL,
                  rating NUMERIC, rating_count INT, weekly_sales INT,
                  sku TEXT, description TEXT, created_at TIMESTAMPTZ
product_reviews → id SERIAL, product_id UUID, rating INT, content TEXT, review_date TEXT, created_at TIMESTAMPTZ
product_price_history → id SERIAL, product_id UUID, old_price NUMERIC, new_price NUMERIC, change_reason TEXT, changed_at TIMESTAMPTZ
orders          → id UUID, store_id UUID, customer_name TEXT, status TEXT,
                  total_amount NUMERIC, ordered_at TIMESTAMPTZ
                  status değerleri: pending|confirmed|shipped|delivered|cancelled|refunded
order_items     → id SERIAL, order_id UUID, product_id UUID, product_name TEXT,
                  unit_price NUMERIC, quantity INT, line_total NUMERIC
stock_movements → id SERIAL, product_id UUID, movement_type TEXT, quantity INT,
                  stock_after INT, moved_at TIMESTAMPTZ
                  movement_type: in|out|adjustment|return
product_daily_metrics → id SERIAL, product_id UUID, date DATE, views INT, clicks INT,
                        add_to_cart INT, purchases INT, revenue NUMERIC, conversion_rate NUMERIC
store_daily_metrics   → id SERIAL, store_id UUID, date DATE, total_orders INT,
                        total_revenue NUMERIC, total_visitors INT, new_customers INT
campaign_performance  → id SERIAL, store_id UUID, campaign_name TEXT, campaign_type TEXT,
                        start_date DATE, end_date DATE, total_orders INT,
                        total_revenue NUMERIC, cost NUMERIC, roi NUMERIC
                        DİKKAT: product_id kolonu YOK — mağaza bazlı tablo, ürün bazlı değil.
                        Kampanya + ürün birlikte sorulursa: kampanya tarihlerini al,
                        o tarih aralığında order_items'tan ürün satışlarına bak.
customers       → id SERIAL, store_id UUID, name TEXT, email TEXT, total_orders INT,
                  total_spent NUMERIC, last_order_at TIMESTAMPTZ, tags TEXT[]

SORU TIPINE GÖRE TABLO:
  Stok soruları        → products (stock_quantity, stock_alert_level)
  Kar/fiyat soruları   → products (price, cost_price, discount)
  Yorum soruları       → product_reviews JOIN products
  Satış soruları       → order_items JOIN orders JOIN products
  Sipariş soruları     → orders
  Fiyat geçmişi        → product_price_history JOIN products
  Kampanya analizi     → campaign_performance (mevcut kampanya performansı)
  Kampanya önerisi     → products + order_items JOIN orders (stok, marj, satış verisiyle karar ver)
                         "hangi ürüne kampanya yapmalıyım" = products tablosundan marj+stok+satış çek
  Müşteri analizi      → customers
  Mağaza bilgisi       → stores
  Günlük metrikler     → product_daily_metrics JOIN products
"""

_SYSTEM_PROMPT = f"""Sen bir PostgreSQL uzmanısın. Türkçe soruyu okur, doğru SQL SELECT sorgusunu yazarsın.

{_SCHEMA}

KURALLAR:
1. SADECE SELECT. INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE/CREATE kesinlikle yasak.
2. products/orders/order_items/customers tablolarında MUTLAKA:
   store_id = ANY(CAST(:store_ids AS uuid[])) filtresi ekle.
3. stores tablosunda MUTLAKA: user_id = :user_id filtresi ekle.
4. product_reviews, order_items gibi join tablolarında üst tablonun store_id filtresini JOIN ile uygula.
5. LIMIT ekle — max 50 satır.
6. Türkçeyi anla: "malım"=stok, "cebime ne giriyor"=kar, "puan"=rating, "yorum"=product_reviews, "satılan"=order_items.
7. Ürün adı aramasında her kelimeyi AYRI AYRI ara — tek cümle olarak arama.
   YANLIŞ: p.name ILIKE '%Anker powerbank%'
   DOĞRU:  p.name ILIKE '%Anker%' AND p.name ILIKE '%powerbank%'
   Ya da daha güvenli: p.name ILIKE '%Anker%' (marka yeterli çoğu zaman)
8. Hesaplamalar PostgreSQL'de: kar=(price-cost_price), marj=kar/price*100.
9. Kolon aliasları Türkçe ver: AS kar, AS marj_yuzde, AS stok_adeti vb.
10. UUID karşılaştırmalarında MUTLAKA CAST(:store_ids AS uuid[]) kullan — string değil.

SADECE JSON döndür, başka hiçbir şey yazma:
{{
  "sql": "SELECT ... FROM ... WHERE store_id = ANY(CAST(:store_ids AS uuid[])) ...",
  "description": "kısa açıklama",
  "model_tier": "mini"
}}
"""

_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|CREATE|GRANT|REVOKE"
    r"|EXECUTE|EXEC|CALL|pg_read_file|pg_ls_dir|COPY)\b",
    re.IGNORECASE,
)


def _is_safe(sql: str) -> bool:
    s = sql.strip().upper()
    if not (s.startswith("SELECT") or s.startswith("WITH")):
        return False
    if _FORBIDDEN.search(sql):
        return False
    return True


def _ensure_uuid_cast(sql: str) -> str:
    """store_ids parametresi içeren tüm ANY(:store_ids) ifadelerini uuid[] cast'e çevir."""
    # ANY(:store_ids) → ANY(CAST(:store_ids AS uuid[]))
    sql = re.sub(
        r"ANY\s*\(\s*:store_ids\s*\)",
        "ANY(CAST(:store_ids AS uuid[]))",
        sql,
        flags=re.IGNORECASE,
    )
    # ::text = ANY(...) gibi text cast varsa kaldır
    sql = re.sub(
        r"::text\s*(=\s*ANY\s*\(CAST\(:store_ids AS uuid\[\]\)\))",
        r" \1",
        sql,
        flags=re.IGNORECASE,
    )
    return sql


def _ensure_limit(sql: str) -> str:
    """LIMIT yoksa ekle."""
    if re.search(r"\bLIMIT\b", sql, re.IGNORECASE):
        return sql
    sql = sql.rstrip().rstrip(";")
    return sql + " LIMIT 50"


def nl_to_sql(
    question: str,
    store_ids: list[str],
    user_id: int,
    *,
    api_key: str | None = None,
    is_admin: bool = False,
) -> dict[str, Any]:
    q = (question or "").strip()
    if not q:
        return _empty("Soru boş.")

    key = api_key or os.environ.get("OPENAI_API_KEY")
    if not key:
        return _empty("API key yok.")

    # 1) LLM'den SQL al
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key, timeout=12)
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": q},
            ],
            temperature=0,
            max_tokens=500,
            response_format={"type": "json_object"},
        )
        raw = (completion.choices[0].message.content or "").strip()
        if not raw:
            return _empty("LLM boş döndü.")
        parsed = json.loads(raw)
    except Exception as exc:
        print(f"[NL_TO_SQL] LLM failed: {exc}")
        return _empty(f"LLM hatası: {exc}")

    sql = (parsed.get("sql") or "").strip()
    description = parsed.get("description") or ""
    model_tier = parsed.get("model_tier") or "mini"

    # 2) Güvenlik
    if not _is_safe(sql):
        print(f"[NL_TO_SQL] GÜVENSİZ SQL reddedildi: {sql[:120]}")
        return _empty("Güvenlik: sadece SELECT sorgularına izin verilir.")

    # 3) UUID cast ve LIMIT garantisi
    sql = _ensure_uuid_cast(sql)
    sql = _ensure_limit(sql)

    params: dict[str, Any] = {
        "store_ids": list(store_ids or []),
        "user_id": int(user_id),
    }

    # 4) SQL çalıştır
    try:
        from app.core.database import SessionLocal
        with SessionLocal() as session:
            result = session.execute(text(sql), params)
            cols = list(result.keys())
            rows = [dict(zip(cols, row)) for row in result.fetchall()]
    except Exception as exc:
        print(f"[NL_TO_SQL] SQL failed: {exc}\nSQL: {sql}")
        return _empty(f"Sorgu hatası: {exc}")

    if not rows:
        return {
            "rows": [],
            "formatted": "(Bu sorgu için kayıt bulunamadı.)",
            "sql": sql,
            "description": description,
            "model_tier": model_tier,
            "row_count": 0,
            "error": None,
        }

    formatted = _format_rows(rows, cols)

    return {
        "rows": rows,
        "formatted": formatted,
        "sql": sql,
        "description": description,
        "model_tier": model_tier,
        "row_count": len(rows),
        "error": None,
    }


def _format_rows(rows: list[dict], cols: list[str]) -> str:
    # UUID kolonlarını atla — gereksiz token
    skip_cols = {c for c in cols if c in ("id", "product_id", "store_id", "order_id")}
    lines = []
    for row in rows[:50]:
        parts = []
        for col in cols:
            if col in skip_cols:
                continue
            val = row.get(col)
            if val is None:
                continue
            # Sayısal değerleri 2 ondalıkla formatla
            if isinstance(val, float):
                val = f"{val:.2f}".rstrip("0").rstrip(".")
            elif hasattr(val, "__float__"):
                try:
                    val = f"{float(val):.2f}".rstrip("0").rstrip(".")
                except Exception:
                    pass
            parts.append(f"{col}: {val}")
        if parts:
            lines.append(" | ".join(parts))
    return "\n".join(lines)


def _empty(reason: str) -> dict[str, Any]:
    return {
        "rows": [],
        "formatted": "",
        "sql": "",
        "description": reason,
        "model_tier": "mini",
        "row_count": 0,
        "error": reason,
    }