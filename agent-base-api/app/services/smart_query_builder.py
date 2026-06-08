"""Smart Query Builder — intent → tek bir SELECT sorgu, parametreli.

YALNIZCA SELECT. SQL injection yok — tüm değerler sqlalchemy text() bind ile geçer.
Sonuçlar token-efficient bir metin bloğuna formatlanır (format_for_llm).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text


_PRODUCT_FILTER = " AND p.name ILIKE '%' || :product_slug || '%'"

QUERY_TEMPLATES: dict[str, str] = {
    "stock_check": """
        SELECT name, stock_quantity, stock_alert_level, is_active, status
        FROM products
        WHERE store_id::text = ANY(:store_ids)
        {product_filter}
        ORDER BY stock_quantity ASC
        LIMIT 100
    """,
    "profit_analysis": """
        SELECT name, price, cost_price,
               ROUND((price - COALESCE(cost_price, 0))::numeric, 2) AS kar,
               CASE WHEN price > 0 AND cost_price IS NOT NULL
                    THEN ROUND(((price - cost_price) / price * 100)::numeric, 1)
                    ELSE NULL END AS marj_yuzde
        FROM products
        WHERE store_id::text = ANY(:store_ids)
        {product_filter}
        ORDER BY marj_yuzde DESC NULLS LAST
        LIMIT 50
    """,
    "review_analysis": """
        SELECT p.name AS urun_adi,
               p.rating AS urun_rating,
               p.rating_count AS urun_rating_count,
               r.rating, r.content, r.review_date
        FROM product_reviews r
        JOIN products p ON p.id = r.product_id
        WHERE p.store_id::text = ANY(:store_ids)
        {product_filter}
        ORDER BY r.created_at DESC
        LIMIT 25
    """,
    "sales_analysis": """
        SELECT p.name,
               COUNT(oi.id) AS siparis_sayisi,
               SUM(oi.quantity) AS toplam_adet,
               ROUND(SUM(oi.line_total)::numeric, 2) AS toplam_gelir
        FROM order_items oi
        JOIN products p ON p.id = oi.product_id
        JOIN orders o ON o.id = oi.order_id
        WHERE p.store_id::text = ANY(:store_ids)
          AND o.ordered_at >= NOW() - INTERVAL '30 days'
          AND o.status NOT IN ('cancelled','refunded')
        {product_filter}
        GROUP BY p.name
        ORDER BY toplam_gelir DESC NULLS LAST
        LIMIT 20
    """,
    "price_history": """
        SELECT p.name, ph.old_price, ph.new_price, ph.change_reason, ph.changed_at
        FROM product_price_history ph
        JOIN products p ON p.id = ph.product_id
        WHERE p.store_id::text = ANY(:store_ids)
        {product_filter}
        ORDER BY ph.changed_at DESC
        LIMIT 20
    """,
    "campaign_analysis": """
        SELECT campaign_name, campaign_type, start_date, end_date,
               total_orders, total_revenue, roi
        FROM campaign_performance
        WHERE store_id::text = ANY(:store_ids)
        ORDER BY start_date DESC
        LIMIT 10
    """,
    "customer_analysis": """
        SELECT name, total_orders, total_spent, last_order_at, tags
        FROM customers
        WHERE store_id::text = ANY(:store_ids)
        ORDER BY total_spent DESC NULLS LAST
        LIMIT 20
    """,
    "store_info": """
        SELECT name, rating
        FROM stores
        WHERE user_id = :user_id
        ORDER BY created_at DESC
    """,
    "general_overview": """
        SELECT s.name AS magaza,
               COUNT(DISTINCT p.id) AS urun_sayisi,
               ROUND(AVG(p.rating)::numeric, 1) AS ort_rating,
               COALESCE(SUM(p.stock_quantity), 0) AS toplam_stok
        FROM stores s
        LEFT JOIN products p ON p.store_id = s.id
        WHERE s.user_id = :user_id
        GROUP BY s.name
    """,
    "admin_platform_overview": """
        SELECT s.name AS magaza,
               COUNT(DISTINCT p.id) AS urun_sayisi,
               COALESCE(SUM(o.total_amount), 0) AS toplam_ciro,
               ROUND(AVG(pr.rating)::numeric, 1) AS ort_rating
        FROM stores s
        LEFT JOIN products p ON p.store_id = s.id
        LEFT JOIN orders o ON o.store_id = s.id
            AND o.ordered_at >= NOW() - INTERVAL '30 days'
        LEFT JOIN product_reviews pr ON pr.product_id = p.id
        GROUP BY s.name
        ORDER BY toplam_ciro DESC
        LIMIT 20
    """,
    "price_analysis": """
        SELECT name, price, discount, discount_type, rating, stock_quantity
        FROM products
        WHERE store_id::text = ANY(:store_ids)
        {product_filter}
        ORDER BY price DESC NULLS LAST
        LIMIT 50
    """,
}


def _row_to_dict(row) -> dict:
    try:
        return dict(row._mapping)  # SQLAlchemy 2.0 Row
    except AttributeError:
        try:
            return dict(row)
        except Exception:
            return {}


def run_query(
    intent: str,
    store_ids: list[str],
    user_id: int,
    product_slug: str | None = None,
    is_admin: bool = False,
) -> dict[str, Any]:
    """SELECT çalıştır, rows + formatted metin döndür.

    Returns: {"rows": [...], "formatted": "...", "intent": ..., "row_count": N}
    Hata olursa boş sonuç döner.
    """
    # admin scope="all" + general → admin overview kullan
    effective_intent = intent
    if is_admin and intent == "general_overview" and not store_ids:
        effective_intent = "admin_platform_overview"

    template = QUERY_TEMPLATES.get(effective_intent)
    if not template:
        return _empty(effective_intent)

    needs_stores = ":store_ids" in template
    needs_user = ":user_id" in template
    if needs_stores and not store_ids and not is_admin:
        return _empty(effective_intent)

    product_filter_sql = _PRODUCT_FILTER if product_slug and "{product_filter}" in template else ""
    sql = template.format(product_filter=product_filter_sql) if "{product_filter}" in template else template

    params: dict[str, Any] = {}
    if needs_stores:
        params["store_ids"] = list(store_ids or [])
    if needs_user:
        params["user_id"] = int(user_id)
    if product_slug and "{product_filter}" in template:
        params["product_slug"] = product_slug

    try:
        from app.core.database import SessionLocal

        with SessionLocal() as session:
            result = session.execute(text(sql), params)
            rows = [_row_to_dict(r) for r in result.all()]
    except Exception as exc:
        print(f"[QUERY_BUILDER] {effective_intent} failed: {exc}")
        return _empty(effective_intent)

    return {
        "intent": effective_intent,
        "rows": rows,
        "row_count": len(rows),
        "formatted": format_for_llm(effective_intent, rows),
    }


def _empty(intent: str) -> dict[str, Any]:
    return {"intent": intent, "rows": [], "row_count": 0, "formatted": ""}


def _fmt_num(v) -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
        if f.is_integer():
            return str(int(f))
        return f"{f:.2f}"
    except (TypeError, ValueError):
        return str(v)


def format_for_llm(intent: str, rows: list[dict]) -> str:
    """Intent başına minimal, token-efficient metin döndür."""
    if not rows:
        return "(Veri yok.)"

    if intent == "stock_check":
        lines = [
            f"{r.get('name', '?')}: {_fmt_num(r.get('stock_quantity'))} adet"
            + (" (KRİTİK)" if r.get('stock_quantity') is not None and r.get('stock_alert_level') is not None and int(r['stock_quantity']) <= int(r['stock_alert_level']) else "")
            + (" [pasif]" if r.get("is_active") is False else "")
            for r in rows[:50]
        ]
        return " | ".join(lines)

    if intent == "profit_analysis":
        out = []
        for r in rows[:30]:
            parts = [f"{r.get('name', '?')}"]
            if r.get("price") is not None:
                parts.append(f"fiyat {_fmt_num(r['price'])} TL")
            if r.get("cost_price") is not None:
                parts.append(f"maliyet {_fmt_num(r['cost_price'])} TL")
            if r.get("kar") is not None:
                parts.append(f"kar {_fmt_num(r['kar'])} TL")
            if r.get("marj_yuzde") is not None:
                parts.append(f"marj %{_fmt_num(r['marj_yuzde'])}")
            out.append(" — ".join(parts))
        return "\n".join(out)

    if intent == "review_analysis":
        # Ürün bazlı genel rating özeti (DB'den gelen gerçek değerler)
        product_ratings: dict[str, tuple] = {}
        for r in rows:
            urun = r.get("urun_adi", "?")
            if urun not in product_ratings:
                product_ratings[urun] = (r.get("urun_rating"), r.get("urun_rating_count"))

        header_lines = []
        for urun, (urun_rating, urun_count) in product_ratings.items():
            if urun_rating is not None:
                count_str = f" ({urun_count} oy)" if urun_count else ""
                header_lines.append(
                    f"{urun}: genel rating {_fmt_num(urun_rating)}/5{count_str}"
                )

        # Bireysel yorumlar
        review_lines = []
        for r in rows[:25]:
            content = (r.get("content") or "").strip()[:200]
            yorum_rating = r.get("rating")
            date = r.get("review_date") or ""
            review_lines.append(
                f"[{yorum_rating}/5] {r.get('urun_adi', '?')} {date}: {content}"
            )

        header = (
            "ÜRÜN RATING'LERİ (DB'den gelen gerçek değerler — aynen kullan, değiştirme):\n"
            + "\n".join(header_lines)
        )
        reviews = "BİREYSEL YORUMLAR:\n" + "\n".join(review_lines)
        return header + "\n\n" + reviews

    if intent == "sales_analysis":
        out = []
        for r in rows[:20]:
            out.append(
                f"{r.get('name', '?')}: {_fmt_num(r.get('siparis_sayisi'))} sipariş, "
                f"{_fmt_num(r.get('toplam_adet'))} adet, "
                f"{_fmt_num(r.get('toplam_gelir'))} TL gelir"
            )
        return "\n".join(out)

    if intent == "price_history":
        out = []
        for r in rows[:20]:
            out.append(
                f"{r.get('name', '?')}: {_fmt_num(r.get('old_price'))} → "
                f"{_fmt_num(r.get('new_price'))} TL "
                f"({r.get('change_reason') or '—'}) {r.get('changed_at')}"
            )
        return "\n".join(out)

    if intent == "campaign_analysis":
        out = []
        for r in rows[:10]:
            roi = r.get("roi")
            roi_s = f", ROI {_fmt_num(roi)}" if roi is not None else ""
            out.append(
                f"{r.get('campaign_name', '?')} ({r.get('campaign_type') or '—'}) "
                f"{r.get('start_date')}→{r.get('end_date') or 'devam'}: "
                f"{_fmt_num(r.get('total_orders'))} sipariş, "
                f"{_fmt_num(r.get('total_revenue'))} TL{roi_s}"
            )
        return "\n".join(out)

    if intent == "customer_analysis":
        out = []
        for r in rows[:20]:
            tags = r.get("tags") or []
            tag_s = f" [{', '.join(tags)}]" if tags else ""
            out.append(
                f"{r.get('name', '?')}: {_fmt_num(r.get('total_orders'))} sipariş, "
                f"{_fmt_num(r.get('total_spent'))} TL toplam, "
                f"son {r.get('last_order_at') or '—'}{tag_s}"
            )
        return "\n".join(out)

    if intent == "store_info":
        out = [
            f"{r.get('name', '?')}"
            + (f" — ⭐ {_fmt_num(r['rating'])}" if r.get("rating") is not None else "")
            for r in rows
        ]
        return " | ".join(out)

    if intent in ("general_overview", "admin_platform_overview"):
        out = []
        for r in rows:
            parts = [r.get("magaza", "?")]
            if r.get("urun_sayisi") is not None:
                parts.append(f"{_fmt_num(r['urun_sayisi'])} ürün")
            if r.get("ort_rating") is not None:
                parts.append(f"⭐ {_fmt_num(r['ort_rating'])}")
            if r.get("toplam_stok") is not None:
                parts.append(f"{_fmt_num(r['toplam_stok'])} stok")
            if r.get("toplam_ciro") is not None:
                parts.append(f"{_fmt_num(r['toplam_ciro'])} TL ciro")
            out.append(" — ".join(parts))
        return "\n".join(out)

    if intent == "price_analysis":
        out = []
        for r in rows[:30]:
            parts = [f"{r.get('name', '?')}: {_fmt_num(r.get('price'))} TL"]
            if r.get("discount"):
                parts.append(f"indirim {_fmt_num(r['discount'])}")
            if r.get("rating") is not None:
                parts.append(f"⭐ {_fmt_num(r['rating'])}")
            if r.get("stock_quantity") is not None:
                parts.append(f"stok {_fmt_num(r['stock_quantity'])}")
            out.append(" — ".join(parts))
        return "\n".join(out)

    # default
    return "\n".join(str(r) for r in rows[:20])