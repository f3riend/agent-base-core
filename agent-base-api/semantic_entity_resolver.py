"""
Semantic entity resolver — Türkçe doğal ifadeleri gerçek entity ID'lerine çevir.

Kullanım örnekleri:
    "Çanakkale hesabı"           → Store(handle='canakkale')
    "yüksek stoklu ürünler"      → list[Item] order by stock DESC
    "en iyi performans gösteren  → Store with highest sales sum
     mağaza"
    "olumsuz yorumlu ürünler"    → list[Item] with neg_review_count > 0

Resolver iki amaçla kullanılır:
    1. StructuredRule.target.entity_filters ve trigger.filters runtime'da
       bu resolver'a verilebilir; "value": "yüksek stoklu" gibi semantic
       string'ler concrete query'lere döner.
    2. AI Operatör chat ve dashboard'tan operatör "yüksek stoklu ürünler
       neler" diye sorduğunda doğrudan resolver kullanılabilir.

Resolver hibrit (deterministic regex + opsiyonel LLM); ana mantık
deterministic çünkü runtime'da güvenli, hızlı, replayable olmalı. LLM
sadece tanımadığı ifadeleri sınıflandırmak için fallback olarak çağrılır
(NL_RESOLVER_USE_LLM=1 ile aktive olur; default=0).
"""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Literal


_FAKE_DB_PATH = os.environ.get("FAKE_API_DB_PATH", "fake_ai_api.db")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


EntityKind = Literal["store", "item", "order", "review", "campaign"]


@dataclass
class ResolvedEntities:
    """Resolver çıktısı — bir semantic query'nin çözülmüş hâli."""
    kind: EntityKind
    ids: list[int] = field(default_factory=list)
    samples: list[dict] = field(default_factory=list)  # ilk 5 örnek
    interpretation: str = ""    # operatöre açıklama
    confidence: float = 1.0
    used_filter: dict = field(default_factory=dict)
    raw_query: str = ""

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "ids": self.ids,
            "samples": self.samples[:5],
            "interpretation": self.interpretation,
            "confidence": self.confidence,
            "used_filter": self.used_filter,
            "raw_query": self.raw_query,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_query(sql: str, params: tuple = ()) -> list[dict]:
    try:
        conn = sqlite3.connect(_FAKE_DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except sqlite3.Error as exc:
        print(f"[RESOLVER] fake_query error: {exc}")
        return []


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


# ---------------------------------------------------------------------------
# Pattern → resolver function table
# ---------------------------------------------------------------------------


def _resolve_high_stock(query: str, limit: int = 5) -> ResolvedEntities:
    items = _fake_query(
        "SELECT id, name, stock, sales FROM items "
        "WHERE stock IS NOT NULL ORDER BY stock DESC LIMIT ?", (limit,),
    )
    return ResolvedEntities(
        kind="item",
        ids=[i["id"] for i in items],
        samples=items,
        interpretation=f"En yüksek stoklu {len(items)} ürün",
        used_filter={"order_by": "stock DESC", "limit": limit},
        raw_query=query,
    )


def _resolve_low_stock(query: str, threshold: int = 10) -> ResolvedEntities:
    items = _fake_query(
        "SELECT id, name, stock, sales FROM items "
        "WHERE stock IS NOT NULL AND stock < ? ORDER BY stock ASC", (threshold,),
    )
    return ResolvedEntities(
        kind="item",
        ids=[i["id"] for i in items],
        samples=items,
        interpretation=f"Stoğu {threshold} adedin altında olan {len(items)} ürün",
        used_filter={"stock_lt": threshold},
        raw_query=query,
    )


def _resolve_top_selling(query: str, limit: int = 5) -> ResolvedEntities:
    items = _fake_query(
        "SELECT id, name, sales, stock FROM items "
        "WHERE sales > 0 ORDER BY sales DESC LIMIT ?", (limit,),
    )
    return ResolvedEntities(
        kind="item",
        ids=[i["id"] for i in items],
        samples=items,
        interpretation=f"En çok satan {len(items)} ürün",
        used_filter={"order_by": "sales DESC", "limit": limit},
        raw_query=query,
    )


def _resolve_top_store(query: str) -> ResolvedEntities:
    # En çok satışı yapan mağaza
    rows = _fake_query(
        """
        SELECT s.id, s.name, COALESCE(SUM(i.sales), 0) AS total_sales
        FROM stores s LEFT JOIN items i ON i.store_id = s.id
        GROUP BY s.id ORDER BY total_sales DESC LIMIT 5
        """
    )
    return ResolvedEntities(
        kind="store",
        ids=[r["id"] for r in rows],
        samples=rows,
        interpretation=f"En yüksek satış toplamına sahip {len(rows)} mağaza",
        used_filter={"order_by": "sum(items.sales) DESC"},
        raw_query=query,
    )


def _resolve_store_by_handle(query: str, handle: str) -> ResolvedEntities:
    # Handle veya isim üzerinden mağaza ara
    rows = _fake_query(
        "SELECT id, name, owner, instagram FROM stores "
        "WHERE LOWER(name) LIKE ? OR LOWER(owner) LIKE ? "
        "OR LOWER(instagram) LIKE ? LIMIT 10",
        (f"%{handle}%", f"%{handle}%", f"%{handle}%"),
    )
    return ResolvedEntities(
        kind="store",
        ids=[r["id"] for r in rows],
        samples=rows,
        interpretation=(
            f"'{handle}' aramasına uyan {len(rows)} mağaza"
            if rows else f"'{handle}' eşleşmesi bulunamadı"
        ),
        used_filter={"handle_like": handle},
        confidence=0.85 if rows else 0.3,
        raw_query=query,
    )


def _resolve_neg_review_items(query: str) -> ResolvedEntities:
    """Olumsuz yorumu olan ürünler — review tablosu üzerinden join."""
    rows = _fake_query(
        """
        SELECT i.id, i.name, COUNT(r.id) AS neg_count
        FROM items i
        JOIN reviews r ON r.item_id = i.id
        WHERE r.sentiment = 'negative'
        GROUP BY i.id ORDER BY neg_count DESC LIMIT 10
        """
    )
    return ResolvedEntities(
        kind="item",
        ids=[r["id"] for r in rows],
        samples=rows,
        interpretation=f"Olumsuz yorumlu {len(rows)} ürün",
        used_filter={"reviews.sentiment": "negative"},
        raw_query=query,
    )


def _resolve_trending(query: str, limit: int = 5) -> ResolvedEntities:
    # Son 7 gündeki satış artışı yüksek olanlar — basitleştirilmiş heuristik
    items = _fake_query(
        "SELECT id, name, sales, stock FROM items "
        "WHERE sales > 5 ORDER BY (sales * 1.0 / (stock + 1)) DESC LIMIT ?",
        (limit,),
    )
    return ResolvedEntities(
        kind="item",
        ids=[i["id"] for i in items],
        samples=items,
        interpretation=f"Yüksek sales/stock oranlı (trending) {len(items)} ürün",
        used_filter={"order_by": "sales/stock DESC"},
        raw_query=query,
    )


# Pattern → resolver tablosu. İlk eşleşen kazanır.
_PATTERN_HANDLERS: tuple[tuple[re.Pattern, Any], ...] = (
    (re.compile(r"\b(?:y[üu]ksek|en\s+[çc]ok)\s+stok\w*\s+[üu]r[üu]n\w*\b|"
                r"\bstok\w*\s+en\s+(?:[çc]ok|y[üu]ksek)\w*\b|"
                r"\ben\s+(?:[çc]ok|y[üu]ksek)\s+stok\w*\b", re.IGNORECASE),
     _resolve_high_stock),
    (re.compile(r"\bd[üu][şs][üu]k\s+stok\w*\s*[üu]r[üu]n\w*\b|"
                r"\bstok\w*\s+(?:az|d[üu][şs][üu]k|kritik)\w*\b|"
                r"\baz\s+kalan\s+[üu]r[üu]n\w*\b", re.IGNORECASE),
     _resolve_low_stock),
    (re.compile(r"\ben\s+(?:[çc]ok|iyi)\s+sat[ıi]\w*\s*[üu]r[üu]n\w*\b|"
                r"\bsat[ıi][şs]\w*\s+y[üu]ksek\s+[üu]r[üu]n\w*\b|"
                r"\btop\s+(?:satan|sat[ıi][şs])\w*\b", re.IGNORECASE),
     _resolve_top_selling),
    (re.compile(r"\ben\s+(?:iyi|y[üu]ksek)\s+performans\s+g[öo]steren\s+ma[ğg]aza\w*\b|"
                r"\ben\s+(?:iyi|y[üu]ksek)\s+ma[ğg]aza\w*\b|"
                r"\bperformans\s+lider\w*\s+ma[ğg]aza\w*\b", re.IGNORECASE),
     _resolve_top_store),
    (re.compile(r"\bolumsuz\s+yorum\w*\s+[üu]r[üu]n\w*\b|"
                r"[üu]r[üu]n\w*\s+olumsuz\s+yorum\w*\b|"
                r"\bnegatif\s+yorum\w*\s+[üu]r[üu]n\w*\b", re.IGNORECASE),
     _resolve_neg_review_items),
    (re.compile(r"\btrend\w*\s+[üu]r[üu]n\w*\b|"
                r"\bviral\s+[üu]r[üu]n\w*\b|"
                r"\b[öo]ne\s+[çc][ıi]kan\s+[üu]r[üu]n\w*\b", re.IGNORECASE),
     _resolve_trending),
)


# Şehir / handle / hesap pattern'ı ayrı çünkü dinamik string yakalar.
_HANDLE_PATTERN = re.compile(
    r"(?P<handle>[A-Za-zÇĞİÖŞÜçğıöşü0-9_]{2,32})\s+(?:hesab[ıi]n[dt]a|"
    r"ma[ğg]azas[ıi]n[dt]a|hesab[ıi]|ma[ğg]azas[ıi])",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve(query: str, *, context: dict | None = None) -> ResolvedEntities:
    """Tek bir semantic ifadeyi gerçek entity'lere çevir.

    Sıra:
        1. Sabit kalıp regex'leri (high_stock, low_stock, top_selling, vs.)
        2. Hesap/handle pattern'ı (Çanakkale hesabı, demo hesab)
        3. Eğer NL_RESOLVER_USE_LLM=1 ve hiçbir pattern eşleşmediyse
           OpenAI ile zayıf bir sınıflandırma denemesi.
        4. Aksi halde: boş ResolvedEntities döner (confidence=0).
    """
    q = _normalize(query)
    if not q:
        return ResolvedEntities(kind="item", interpretation="boş sorgu",
                                confidence=0, raw_query=query)

    # 1) Pattern tablosu
    for pattern, handler in _PATTERN_HANDLERS:
        if pattern.search(q):
            return handler(query)

    # 2) Handle araması
    m = _HANDLE_PATTERN.search(q)
    if m:
        handle = m.group("handle").strip().lower()
        return _resolve_store_by_handle(query, handle)

    # 3) LLM fallback (opsiyonel)
    if os.environ.get("NL_RESOLVER_USE_LLM", "0") == "1":
        llm = _resolve_with_llm(query)
        if llm is not None:
            return llm

    # 4) Boş
    return ResolvedEntities(
        kind="item",
        interpretation=(
            f"'{query}' ifadesini şu an çözümleyemedim — daha açık yaz "
            "(örn. 'yüksek stoklu ürünler', 'Çanakkale hesabı')."
        ),
        confidence=0.0,
        raw_query=query,
    )


def resolve_filter_value(value: Any, *, context: dict | None = None) -> Any:
    """StructuredRule.target.entity_filters üzerinde resolver kullanımı.

    Eğer value bir string ve `$semantic:` prefix'i taşıyorsa resolver
    çalıştırılır ve sonuç ID listesi döner. Aksi halde value olduğu gibi
    döner.

    Örnek rule.target.entity_filters:
        {"item_id": "$semantic:yüksek stoklu ürünler"}
    """
    if not isinstance(value, str):
        return value
    if not value.startswith("$semantic:"):
        return value
    query = value[len("$semantic:"):].strip()
    resolved = resolve(query, context=context)
    return resolved.ids


def _resolve_with_llm(query: str) -> ResolvedEntities | None:
    """LLM fallback — sadece NL_RESOLVER_USE_LLM=1 iken çağrılır."""
    if not os.environ.get("OPENAI_API_KEY"):
        return None
    try:
        from openai import OpenAI
        client = OpenAI(timeout=8)
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": (
                    "Sen bir entity resolver'sın. Türkçe ifade verildiğinde "
                    "hangi kategoriye girdiğini tahmin et: high_stock, "
                    "low_stock, top_selling, top_store, neg_review_items, "
                    "trending, store_by_handle, none. JSON döndür: "
                    '{"category": "...", "handle": null | "..."}'
                )},
                {"role": "user", "content": query},
            ],
            temperature=0.0, max_tokens=80,
        )
        import json
        parsed = json.loads(completion.choices[0].message.content or "{}")
        category = parsed.get("category")
        if category == "high_stock":   return _resolve_high_stock(query)
        if category == "low_stock":    return _resolve_low_stock(query)
        if category == "top_selling":  return _resolve_top_selling(query)
        if category == "top_store":    return _resolve_top_store(query)
        if category == "neg_review_items": return _resolve_neg_review_items(query)
        if category == "trending":     return _resolve_trending(query)
        if category == "store_by_handle" and parsed.get("handle"):
            return _resolve_store_by_handle(query, parsed["handle"])
    except Exception as exc:
        print(f"[RESOLVER LLM] fallback failed: {exc}")
    return None


# ---------------------------------------------------------------------------
# Diagnostic
# ---------------------------------------------------------------------------


def explain_supported() -> list[dict]:
    """UI tooltip için: hangi ifadeleri tanıyoruz?"""
    return [
        {"pattern": "yüksek stoklu ürünler", "result": "stok DESC top 5"},
        {"pattern": "düşük stoklu ürünler",   "result": "stok < 10"},
        {"pattern": "en çok satan ürünler",   "result": "sales DESC top 5"},
        {"pattern": "en iyi mağaza",          "result": "sum(item.sales) DESC"},
        {"pattern": "olumsuz yorumlu ürünler","result": "reviews.sentiment=negative"},
        {"pattern": "trend ürünler",          "result": "sales/stock yüksek"},
        {"pattern": "X hesabı / X mağazası",  "result": "stores like '%X%'"},
    ]
