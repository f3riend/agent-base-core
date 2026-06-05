"""
pg_context_formatter.py — pg_context dict'ini LLM prompt'u için metin bloğuna çevirir.

business_query_router artık tek tip payload üretiyor: `full_context`.
Bu modül o payload'ı (mağazalar + ürünler + her ürünün yorumları + SSS'leri)
açıkça etiketli, sayıları kazara karıştırılması zor bir blok olarak yazar.
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
            if p.get("price") is not None:
                metas.append(f"Fiyat: {p['price']} TL")
            if p.get("discount"):
                metas.append(f"İndirim: {p['discount']}")
            if p.get("stock") is not None:
                metas.append(f"Stok: {p['stock']}")
            if p.get("rating") is not None:
                rc = f" ({p.get('rating_count', 0)} oy)" if p.get("rating_count") else ""
                metas.append(f"⭐ {p['rating']}/5{rc}")
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
    """Operasyonel context şu an tüketilmiyor (router boş döndürüyor).

    İmza ai_synthesizer ile uyumlu kalsın diye duruyor; ileride kargo / onay /
    workflow gibi PG-dışı sinyaller eklendiğinde burada formatlanır.
    """
    if not op_ctx:
        return ""
    return ""
