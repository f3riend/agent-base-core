"""
pg_context_formatter.py — pg_context dict'ini LLM prompt'u için metin bloğuna çevirir.
"""
from __future__ import annotations


def format_pg_context(pg_ctx: dict) -> str:
    if not pg_ctx:
        return ""
    if pg_ctx.get("type") == "full_context":
        return _format_full_context(
            pg_ctx.get("stores", []),
            pg_ctx.get("products", []),
        )
    return ""


def _format_full_context(stores: list[dict], products: list[dict]) -> str:
    lines: list[str] = []

    lines.append("=== MAĞAZALAR ===")
    lines.append(f"MAĞAZA SAYISI: {len(stores)}")
    if stores:
        lines.append("Mağaza listesi (bu sayı mağaza sayısıdır, ürün sayısı DEĞİLDİR):")
        for idx, s in enumerate(stores, start=1):
            rating_str = f" — ⭐ {s['rating']}" if s.get("rating") else ""
            status_str = f" ({s['status']})" if s.get("status") else ""
            lines.append(f"  {idx}. {s.get('name', '—')}{rating_str}{status_str}")
    else:
        lines.append("(Kayıtlı mağaza yok.)")

    lines.append("")
    lines.append("=== ÜRÜNLER ===")
    lines.append(f"ÜRÜN SAYISI: {len(products)}")
    if not products:
        lines.append("(Kayıtlı ürün yok.)")
    else:
        lines.append("Ürün listesi (bu sayı ürün sayısıdır, mağaza sayısı DEĞİLDİR):")
        for idx, p in enumerate(products, start=1):
            head = f"  {idx}. {p.get('name', 'Ürün')}"
            metas: list[str] = []
            if p.get("brand"):
                metas.append(f"Marka: {p['brand']}")
            if p.get("category"):
                metas.append(f"Kategori: {p['category']}")
            if p.get("sku"):
                metas.append(f"SKU: {p['sku']}")

            # Fiyat + maliyet + kar marjı
            price = p.get("price")
            cost = p.get("cost_price")
            if price is not None:
                metas.append(f"Fiyat: {price} TL")
            if cost is not None and price is not None:
                kar = float(price) - float(cost)
                marj = (kar / float(price) * 100) if float(price) > 0 else 0
                metas.append(f"Maliyet: {cost} TL | Kar: {kar:.2f} TL (%{marj:.1f} marj)")
            if p.get("discount"):
                metas.append(f"İndirim: {p['discount']}")

            # stock_quantity önce, stock fallback
            stok = p.get("stock_quantity") if p.get("stock_quantity") is not None else p.get("stock")
            if stok is not None:
                alert = p.get("stock_alert_level") or 5
                stok_str = f"Stok: {stok} adet"
                if int(stok) <= int(alert):
                    stok_str += " (KRİTİK — düşük stok)"
                metas.append(stok_str)

            if p.get("rating") is not None:
                rc = f" ({p.get('rating_count', 0)} oy)" if p.get("rating_count") else ""
                metas.append(f"Rating: {p['rating']}/5{rc}")
            if p.get("weekly_sales") is not None:
                metas.append(f"Haftalık satış: {p['weekly_sales']}")
            if p.get("status"):
                metas.append(f"Durum: {p['status']}")

            lines.append(f"{head} — " + " | ".join(metas) if metas else head)

            desc = (p.get("description") or "").strip()
            if desc:
                lines.append(f"     Açıklama: {desc[:240]}")

            reviews = p.get("reviews") or []
            if reviews:
                lines.append(f"     --- Yorumlar ({len(reviews)} adet) ---")
                for r in reviews:
                    rt = f"[{r['rating']}/5]" if r.get("rating") is not None else ""
                    dt = f"({r['review_date']})" if r.get("review_date") else ""
                    content = (r.get("content") or "").strip()[:280]
                    if content:
                        lines.append(f"     {rt} {dt} {content}".rstrip())

            faqs = p.get("faqs") or []
            if faqs:
                lines.append(f"     --- SSS ({len(faqs)} adet) ---")
                for f in faqs:
                    q = (f.get("question") or "").strip()
                    a = (f.get("answer") or "").strip()[:280]
                    if q:
                        lines.append(f"     S: {q}")
                        if a:
                            lines.append(f"     C: {a}")

            lines.append("")

    lines.append(
        f"ÖZET: Sistemde {len(stores)} mağaza ve {len(products)} ürün var. "
        "Bu iki sayıyı asla birbirinin yerine kullanma."
    )

    return "\n".join(lines)


def format_op_context(op_ctx: dict) -> str:
    if not op_ctx:
        return ""
    return ""