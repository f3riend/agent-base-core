"""nl_to_sql.py — Doğal dil sorusunu PostgreSQL sorgusuna çevirir.

3 katmanlı güvenlik:
  1. JSON şema — LLM tablolar ve kolonları kesin bilir, tahmin etmez
  2. SQL şablon — sık sorular için önceden yazılmış, test edilmiş SQL
  3. Doğrulama — LLM SQL yazdıktan sonra information_schema ile kontrol

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

# ---------------------------------------------------------------------------
# JSON Şema — LLM bunu okur, tahmin etmez
# ---------------------------------------------------------------------------
_SCHEMA_JSON = {
    "stores": {
        "columns": ["id", "user_id", "name", "rating", "status", "logo_url"],
        "pk": "id",
        "filter": "user_id = :user_id",
        "use_for": ["mağaza bilgisi", "mağaza sayısı", "mağaza listesi"],
    },
    "products": {
        "columns": ["id", "store_id", "name", "brand", "category", "price",
                    "cost_price", "discount", "stock_quantity", "stock_alert_level",
                    "is_active", "rating", "rating_count", "weekly_sales",
                    "sku", "description", "created_at"],
        "pk": "id",
        "filter": "store_id = ANY(CAST(:store_ids AS uuid[]))",
        "use_for": ["ürün bilgisi", "stok", "fiyat", "kar", "marj", "rating",
                    "kampanya önerisi", "indirim önerisi"],
    },
    "product_reviews": {
        "columns": ["id", "product_id", "rating", "content", "review_date", "created_at"],
        "pk": "id",
        "filter": "JOIN products p ON p.id = product_id WHERE p.store_id = ANY(CAST(:store_ids AS uuid[]))",
        "use_for": ["yorumlar", "müşteri görüşleri", "ürün puanı", "memnuniyet"],
        "no_direct_store_filter": True,
        "warning": "rating kolonunun AVG'ı GERÇEK ürün puanı DEĞİLDİR — sadece elimizdeki metinli yorumların alt kümesi. Ürün puanı/rating için DAİMA products.rating + products.rating_count kullan.",
    },
    "product_price_history": {
        "columns": ["id", "product_id", "old_price", "new_price", "change_reason", "changed_at"],
        "pk": "id",
        "filter": "JOIN products p ON p.id = product_id WHERE p.store_id = ANY(CAST(:store_ids AS uuid[]))",
        "use_for": ["fiyat geçmişi", "fiyat değişimi"],
        "no_direct_store_filter": True,
    },
    "orders": {
        "columns": ["id", "store_id", "customer_name", "status", "total_amount",
                    "discount_amount", "shipping_cost", "payment_method",
                    "ordered_at", "shipped_at", "delivered_at", "cancelled_at"],
        "pk": "id",
        "filter": "store_id = ANY(CAST(:store_ids AS uuid[]))",
        "use_for": ["sipariş", "ciro", "gelir", "satış sayısı"],
        "status_values": ["pending", "confirmed", "shipped", "delivered", "cancelled", "refunded"],
    },
    "order_items": {
        "columns": ["id", "order_id", "product_id", "product_name", "unit_price",
                    "quantity", "discount_pct", "line_total"],
        "pk": "id",
        "filter": "JOIN orders o ON o.id = order_id WHERE o.store_id = ANY(CAST(:store_ids AS uuid[]))",
        "use_for": ["en çok satan", "ürün satış adedi", "ürün geliri"],
        "no_direct_store_filter": True,
        "aggregate_required": True,
    },
    "stock_movements": {
        "columns": ["id", "product_id", "movement_type", "quantity", "stock_after", "note", "moved_at"],
        "pk": "id",
        "movement_types": ["in", "out", "adjustment", "return"],
        "filter": "JOIN products p ON p.id = product_id WHERE p.store_id = ANY(CAST(:store_ids AS uuid[]))",
        "use_for": ["stok hareketi", "stok geçmişi", "giriş çıkış"],
        "no_direct_store_filter": True,
    },
    "product_daily_metrics": {
        "columns": ["id", "product_id", "date", "views", "clicks", "add_to_cart",
                    "purchases", "revenue", "conversion_rate"],
        "pk": "id",
        "filter": "JOIN products p ON p.id = product_id WHERE p.store_id = ANY(CAST(:store_ids AS uuid[]))",
        "use_for": ["görüntülenme", "tıklanma", "sepete ekleme", "günlük metrik"],
        "no_direct_store_filter": True,
    },
    "store_daily_metrics": {
        "columns": ["id", "store_id", "date", "total_orders", "total_revenue",
                    "total_visitors", "new_customers", "returning_customers",
                    "avg_order_value", "cancelled_orders", "refunded_orders"],
        "pk": "id",
        "filter": "store_id = ANY(CAST(:store_ids AS uuid[]))",
        "use_for": ["mağaza günlük istatistik", "ziyaretçi", "günlük ciro"],
    },
    "campaign_performance": {
        "columns": ["id", "store_id", "campaign_name", "campaign_type",
                    "start_date", "end_date", "total_orders", "total_revenue",
                    "total_views", "total_clicks", "cost", "roi"],
        "pk": "id",
        "filter": "store_id = ANY(CAST(:store_ids AS uuid[]))",
        "use_for": ["kampanya performansı", "ROI", "mevcut kampanya sonuçları"],
        "NOT_AVAILABLE": ["product_id", "user_id", "item_id"],
        "warning": "product_id YOKTUR — mağaza bazlı tablo. Ürün bazlı kampanya analizi için order_items kullan.",
    },
    "customers": {
        "columns": ["id", "store_id", "name", "email", "phone", "city", "gender", "age",
                    "total_orders", "total_spent", "first_order_at", "last_order_at", "tags"],
        "pk": "id",
        "filter": "store_id = ANY(CAST(:store_ids AS uuid[]))",
        "use_for": ["müşteri analizi", "VIP müşteri", "sadık müşteri", "müşteri harcama",
                    "şehir bazlı analiz", "cinsiyet analizi", "yaş analizi", "hedef kitle", "müşteri demografisi"],
    },
}

# ---------------------------------------------------------------------------
# Sık kullanılan şablonlar — önceden yazılmış, test edilmiş SQL
# ---------------------------------------------------------------------------
_TEMPLATES = {
    "en_cok_satan": {
        "keywords": ["en çok satan", "çok satılan", "popüler ürün", "en çok satılan"],
        "sql": """
            SELECT p.name, SUM(oi.quantity) AS toplam_satilan,
                   ROUND(SUM(oi.line_total)::numeric, 2) AS toplam_gelir
            FROM order_items oi
            JOIN orders o ON o.id = oi.order_id
            JOIN products p ON p.id = oi.product_id
            WHERE o.store_id = ANY(CAST(:store_ids AS uuid[]))
              AND o.status NOT IN ('cancelled','refunded')
            GROUP BY p.id, p.name
            ORDER BY toplam_satilan DESC
            LIMIT 10
        """,
        "description": "En çok satılan ürünler",
    },
    "yorum_istatistik": {
        "keywords": ["yüzde kaç", "olumlu olumsuz", "kaçı olumlu", "kaçı olumsuz",
                     "yorum istatistik", "yorum dağılım", "memnuniyet oranı",
                     "kaç yıldız", "puan dağılımı", "rapor", "istatistik"],
        "sql": """
            SELECT p.name AS urun,
                   COUNT(*) AS toplam_yorum,
                   COUNT(*) FILTER (WHERE pr.rating >= 4) AS olumlu,
                   COUNT(*) FILTER (WHERE pr.rating = 3) AS notr,
                   COUNT(*) FILTER (WHERE pr.rating <= 2) AS olumsuz,
                   ROUND(100.0 * COUNT(*) FILTER (WHERE pr.rating >= 4) / NULLIF(COUNT(*), 0), 1) AS olumlu_yuzde,
                   ROUND(100.0 * COUNT(*) FILTER (WHERE pr.rating <= 2) / NULLIF(COUNT(*), 0), 1) AS olumsuz_yuzde,
                   ROUND(AVG(pr.rating)::numeric, 2) AS ornek_ortalama
            FROM product_reviews pr
            JOIN products p ON p.id = pr.product_id
            JOIN stores s ON p.store_id = s.id
            WHERE s.user_id = :user_id
              AND p.store_id = ANY(CAST(:store_ids AS uuid[]))
            GROUP BY p.id, p.name
            ORDER BY toplam_yorum DESC
            LIMIT 20
        """,
        "description": "Yorum dağılım istatistiği (olumlu/olumsuz yüzde)",
    },
    "en_iyi_puanli": {
        "keywords": ["en iyi ürün", "en iyi 3 ürün", "en iyi ürünlerim", "en yüksek puan",
                     "en iyi puanlı", "en beğenilen", "en iyi yorumu alan", "puanı en yüksek"],
        "sql": """
            SELECT p.name, p.rating AS puan, p.rating_count AS degerlendirme_sayisi
            FROM products p
            JOIN stores s ON p.store_id = s.id
            WHERE s.user_id = :user_id
              AND p.store_id = ANY(CAST(:store_ids AS uuid[]))
              AND p.is_active = true
            ORDER BY p.rating DESC NULLS LAST, p.rating_count DESC NULLS LAST
            LIMIT 5
        """,
        "description": "En iyi puanlı ürünler",
    },
    "en_cok_degerlendirilen": {
        "keywords": ["en çok değerlendirilen", "en çok yorum alan", "en çok oy",
                     "en popüler", "en çok yorumlanan"],
        "sql": """
            SELECT p.name, p.rating AS puan, p.rating_count AS degerlendirme_sayisi
            FROM products p
            JOIN stores s ON p.store_id = s.id
            WHERE s.user_id = :user_id
              AND p.store_id = ANY(CAST(:store_ids AS uuid[]))
              AND p.is_active = true
            ORDER BY p.rating_count DESC NULLS LAST
            LIMIT 5
        """,
        "description": "En çok değerlendirilen ürünler",
    },
    "stok_durumu": {
        "keywords": ["stok", "elimde mal", "malım var mı", "ürün kaldı mı", "stokta"],
        "sql": """
            SELECT name, stock_quantity AS stok_adeti,
                   stock_alert_level AS kritik_seviye,
                   CASE WHEN stock_quantity <= stock_alert_level
                        THEN 'KRİTİK' ELSE 'Normal' END AS durum
            FROM products
            WHERE store_id = ANY(CAST(:store_ids AS uuid[]))
              AND is_active = true
            ORDER BY stock_quantity ASC
            LIMIT 50
        """,
        "description": "Stok durumu",
    },
    "kar_marji": {
        "keywords": ["kar marjı", "cebime ne giriyor", "kar", "marj", "kâr", "kazanıyorum"],
        "sql": """
            SELECT name,
                   price AS fiyat,
                   cost_price AS maliyet,
                   ROUND((price - COALESCE(cost_price, 0))::numeric, 2) AS kar,
                   CASE WHEN price > 0 AND cost_price IS NOT NULL
                        THEN ROUND(((price - cost_price) / price * 100)::numeric, 2)
                        ELSE NULL END AS marj_yuzde
            FROM products
            WHERE store_id = ANY(CAST(:store_ids AS uuid[]))
              AND is_active = true
            ORDER BY marj_yuzde DESC NULLS LAST
            LIMIT 50
        """,
        "description": "Kar ve marj analizi",
    },
    "genel_ozet": {
        "keywords": ["genel durum", "özet", "kaç ürün", "kaç mağaza", "ne var", "durum nasıl"],
        "sql": """
            SELECT s.name AS magaza,
                   COUNT(DISTINCT p.id) AS urun_sayisi,
                   ROUND(AVG(p.rating)::numeric, 2) AS ort_rating,
                   COALESCE(SUM(p.stock_quantity), 0) AS toplam_stok
            FROM stores s
            LEFT JOIN products p ON p.store_id = s.id AND p.is_active = true
            WHERE s.user_id = :user_id
            GROUP BY s.id, s.name
            ORDER BY urun_sayisi DESC
        """,
        "description": "Genel mağaza özeti",
    },
    "bu_ay_ciro": {
        "keywords": ["bu ay ciro", "bu ay gelir", "bu ay satış", "aylık ciro", "bu ay kaç"],
        "sql": """
            SELECT COALESCE(SUM(total_amount), 0) AS toplam_ciro,
                   COUNT(*) AS siparis_sayisi
            FROM orders
            WHERE store_id = ANY(CAST(:store_ids AS uuid[]))
              AND status NOT IN ('cancelled','refunded')
              AND ordered_at >= DATE_TRUNC('month', NOW())
        """,
        "description": "Bu ay ciro ve sipariş sayısı",
    },
    "yorumlar": {
        "keywords": ["yorum", "müşteri ne diyor", "müşteriler ne düşünüyor", "değerlendirme"],
        "sql": """
            SELECT p.name AS urun_adi,
                   p.rating AS genel_puan,
                   p.rating_count AS yorum_sayisi,
                   r.rating AS yorum_puani,
                   r.content AS yorum,
                   r.review_date AS tarih
            FROM product_reviews r
            JOIN products p ON p.id = r.product_id
            WHERE p.store_id = ANY(CAST(:store_ids AS uuid[]))
            ORDER BY r.created_at DESC
            LIMIT 25
        """,
        "description": "Ürün yorumları",
    },
    "kampanya_onerisi": {
        "keywords": ["kampanya yapmalıyım", "indirim yapmalıyım", "hangi ürüne kampanya",
                     "kampanya öner", "indirim öner"],
        "sql": """
            SELECT p.name,
                   p.price AS fiyat,
                   p.cost_price AS maliyet,
                   ROUND((p.price - p.cost_price)::numeric, 2) AS kar,
                   ROUND(((p.price - p.cost_price) / p.price * 100)::numeric, 2) AS marj_yuzde,
                   p.stock_quantity AS stok,
                   COALESCE(SUM(oi.quantity), 0) AS toplam_satilan
            FROM products p
            LEFT JOIN order_items oi ON oi.product_id = p.id
            LEFT JOIN orders o ON o.id = oi.order_id
                AND o.status NOT IN ('cancelled','refunded')
            WHERE p.store_id = ANY(CAST(:store_ids AS uuid[]))
              AND p.is_active = true
            GROUP BY p.id, p.name, p.price, p.cost_price, p.stock_quantity
            ORDER BY marj_yuzde DESC NULLS LAST
            LIMIT 20
        """,
        "description": "Kampanya için ürün analizi (marj + stok + satış)",
    },
    "kategori_analiz": {
        "keywords": ["hangi kategoride", "kategori bazlı", "kategorilerde kaç", "kategoriler nasıl",
                     "kategori satış", "hangi kategoriler"],
        "sql": """
            SELECT p.category AS kategori,
                   COUNT(DISTINCT p.id) AS urun_sayisi,
                   ROUND(AVG(p.rating)::numeric, 2) AS ort_rating,
                   ROUND(AVG((p.price - COALESCE(p.cost_price,0)) / NULLIF(p.price,0) * 100)::numeric, 2) AS ort_marj,
                   COALESCE(SUM(oi.quantity), 0) AS toplam_satilan,
                   ROUND(COALESCE(SUM(oi.line_total), 0)::numeric, 2) AS toplam_gelir
            FROM products p
            LEFT JOIN order_items oi ON oi.product_id = p.id
            LEFT JOIN orders o ON o.id = oi.order_id
                AND o.status NOT IN ('cancelled','refunded')
            WHERE p.store_id = ANY(CAST(:store_ids AS uuid[]))
              AND p.is_active = true
            GROUP BY p.category
            ORDER BY toplam_gelir DESC NULLS LAST
        """,
        "description": "Kategori bazlı ürün ve satış analizi",
    },
    "fiyat_sirala": {
        "keywords": ["en pahalı", "en ucuz", "fiyat listesi", "fiyatları neler",
                     "fiyat sırala", "en yüksek fiyat", "en düşük fiyat"],
        "sql": """
            SELECT name, brand, category,
                   price AS fiyat,
                   COALESCE(discount, 0) AS indirim,
                   CASE WHEN discount > 0
                        THEN ROUND((price * (1 - discount/100))::numeric, 2)
                        ELSE price END AS indirimli_fiyat,
                   stock_quantity AS stok
            FROM products
            WHERE store_id = ANY(CAST(:store_ids AS uuid[]))
              AND is_active = true
            ORDER BY price DESC
            LIMIT 50
        """,
        "description": "Fiyat listesi ve sıralama",
    },
    "dusuk_stok": {
        "keywords": ["düşük stok", "azalan stok", "tükenmek üzere", "kritik stok",
                     "stok uyarısı", "bitmek üzere", "stok azaldı"],
        "sql": """
            SELECT name, brand,
                   stock_quantity AS stok_adeti,
                   stock_alert_level AS uyari_seviyesi,
                   (stock_quantity - stock_alert_level) AS uyari_farki,
                   price AS fiyat
            FROM products
            WHERE store_id = ANY(CAST(:store_ids AS uuid[]))
              AND is_active = true
              AND stock_quantity <= (stock_alert_level * 3)
            ORDER BY stock_quantity ASC
            LIMIT 20
        """,
        "description": "Düşük stok uyarısı",
    },
    "musteri_analiz": {
        "keywords": ["müşteri", "sadık müşteri", "en çok harcayan", "vip müşteri",
                     "müşterilerim kim", "en iyi müşteri"],
        "sql": """
            SELECT name AS musteri,
                   total_orders AS siparis_sayisi,
                   ROUND(total_spent::numeric, 2) AS toplam_harcama,
                   last_order_at AS son_siparis,
                   tags AS etiketler
            FROM customers
            WHERE store_id = ANY(CAST(:store_ids AS uuid[]))
            ORDER BY total_spent DESC NULLS LAST
            LIMIT 20
        """,
        "description": "Müşteri analizi",
    },
    "fiyat_gecmisi": {
        "keywords": ["fiyat geçmişi", "fiyat değişti mi", "eski fiyat", "fiyat değişimi",
                     "ne zaman değişti", "fiyat tarihi"],
        "sql": """
            SELECT p.name AS urun,
                   ph.old_price AS eski_fiyat,
                   ph.new_price AS yeni_fiyat,
                   ROUND((ph.new_price - ph.old_price)::numeric, 2) AS fark,
                   ph.change_reason AS neden,
                   ph.changed_at AS tarih
            FROM product_price_history ph
            JOIN products p ON p.id = ph.product_id
            WHERE p.store_id = ANY(CAST(:store_ids AS uuid[]))
            ORDER BY ph.changed_at DESC
            LIMIT 20
        """,
        "description": "Fiyat değişim geçmişi",
    },
    "haftalik_satis": {
        "keywords": ["bu hafta", "geçen hafta", "haftalık satış", "son 7 gün",
                     "hafta satış", "7 günlük"],
        "sql": """
            SELECT p.name,
                   SUM(oi.quantity) AS toplam_satilan,
                   ROUND(SUM(oi.line_total)::numeric, 2) AS toplam_gelir,
                   COUNT(DISTINCT o.id) AS siparis_sayisi
            FROM order_items oi
            JOIN orders o ON o.id = oi.order_id
            JOIN products p ON p.id = oi.product_id
            WHERE o.store_id = ANY(CAST(:store_ids AS uuid[]))
              AND o.status NOT IN ('cancelled','refunded')
              AND o.ordered_at >= NOW() - INTERVAL '7 days'
            GROUP BY p.id, p.name
            ORDER BY toplam_satilan DESC
            LIMIT 20
        """,
        "description": "Haftalık satış raporu",
    },
    "birlikte_alinan": {
        "keywords": ["birlikte alınan", "birlikte satılan", "beraber alınan",
                     "hangi ürünler beraber", "sepette birlikte", "kombine ürün"],
        "sql": """
            SELECT p1.name AS urun1,
                   p2.name AS urun2,
                   COUNT(*) AS birlikte_siparis_sayisi
            FROM order_items oi1
            JOIN order_items oi2 ON oi1.order_id = oi2.order_id
                AND oi1.product_id < oi2.product_id
            JOIN products p1 ON p1.id = oi1.product_id
            JOIN products p2 ON p2.id = oi2.product_id
            JOIN orders o ON o.id = oi1.order_id
            WHERE o.store_id = ANY(CAST(:store_ids AS uuid[]))
              AND o.status NOT IN ('cancelled','refunded')
              AND p1.store_id = ANY(CAST(:store_ids AS uuid[]))
              AND p2.store_id = ANY(CAST(:store_ids AS uuid[]))
            GROUP BY p1.id, p1.name, p2.id, p2.name
            ORDER BY birlikte_siparis_sayisi DESC
            LIMIT 10
        """,
        "description": "Birlikte satılan ürün çiftleri",
    },
    "aylik_ciro": {
        "keywords": ["geçen ay", "aylık gelir", "geçen ay ciro", "son 30 gün",
                     "son ay", "önceki ay", "aylık satış"],
        "sql": """
            SELECT DATE_TRUNC('month', ordered_at) AS ay,
                   COUNT(*) AS siparis_sayisi,
                   ROUND(SUM(total_amount)::numeric, 2) AS toplam_ciro
            FROM orders
            WHERE store_id = ANY(CAST(:store_ids AS uuid[]))
              AND status NOT IN ('cancelled','refunded')
              AND ordered_at >= NOW() - INTERVAL '90 days'
            GROUP BY DATE_TRUNC('month', ordered_at)
            ORDER BY ay DESC
        """,
        "description": "Aylık ciro özeti",
    },
}

# ---------------------------------------------------------------------------
# LLM sistem promptu — JSON şema ile
# ---------------------------------------------------------------------------
_SCHEMA_TEXT = "\n".join([
    f"  {tname}({', '.join(tinfo['columns'])})"
    + (f"\n    ⚠️  {tinfo['warning']}" if "warning" in tinfo else "")
    + (f"\n    YOKTUR: {', '.join(tinfo['NOT_AVAILABLE'])}" if "NOT_AVAILABLE" in tinfo else "")
    + f"\n    Kullanım: {', '.join(tinfo['use_for'])}"
    for tname, tinfo in _SCHEMA_JSON.items()
])

_SYSTEM_PROMPT = f"""Sen bir PostgreSQL uzmanısın. Türkçe soruyu okur, doğru SQL SELECT sorgusunu yazarsın.

TABLOLAR VE KOLONLAR (sadece bunlar — başka tablo veya kolon kullanma):
{_SCHEMA_TEXT}

ZORUNLU KURALLAR:
1. SADECE SELECT. INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE/CREATE yasak.
2. products/orders/customers/store_daily_metrics/campaign_performance:
   store_id = ANY(CAST(:store_ids AS uuid[])) filtresi ZORUNLU.
3. stores tablosunda: user_id = :user_id filtresi ZORUNLU.
4. product_reviews/order_items/stock_movements/product_daily_metrics/product_price_history:
   üst tabloya JOIN yap, store_id filtresini oradan uygula.
5. order_items JOIN kullanıyorsan MUTLAKA GROUP BY ekle.
   SUM(oi.quantity) AS toplam_satilan, SUM(oi.line_total) AS toplam_gelir kullan.
6. LIMIT ekle — max 50.
7. Ürün aramasında her kelimeyi ayrı ILIKE: p.name ILIKE '%Anker%' AND p.name ILIKE '%powerbank%'
8. Marj hesabı: ROUND(((price-cost_price)/price*100)::numeric, 2)
9. UUID karşılaştırma: MUTLAKA CAST(:store_ids AS uuid[]) — string değil.
10. Kolon aliasları Türkçe: AS kar, AS marj_yuzde, AS stok_adeti vb.
11. Bir tabloda "YOKTUR" yazıyorsa o kolonu KESİNLİKLE kullanma.
12. Ürün puanı / rating / "kaç puan" sorulduğunda SADECE şu kalıbı kullan:
    SELECT name, rating, rating_count FROM products
    WHERE <ürün adı/marka filtresi> AND store_id = ANY(CAST(:store_ids AS uuid[]))
    products.rating kanonik puandır. rating'i başka bir sayıyla ÇARPMA/BÖLME,
    AVG/SUM/GROUP BY KULLANMA. product_reviews.rating'in ortalamasını da ALMA.
13. Ürün kârı/marjı sorulduğunda SADECE şu kalıbı kullan (satış adedi AYRICA
    sorulmadıkça order_items/orders tablolarına DOKUNMA):
    SELECT name, price, cost_price,
           ROUND(((price - cost_price) / price * 100)::numeric, 2) AS marj_yuzde
    FROM products
    WHERE <ürün adı/marka filtresi> AND store_id = ANY(CAST(:store_ids AS uuid[]))
    order_items/orders'a JOIN YAPMA, SUM/GROUP BY KULLANMA. cost_price NULL ise
    marj NULL döner; bu durumda "maliyet verisi yok" demek DOĞRUdur, sayı UYDURMA.    

SADECE JSON döndür:
{{
  "sql": "SELECT ...",
  "description": "kısa açıklama",
  "model_tier": "mini"
}}
"""

# ---------------------------------------------------------------------------
# Güvenlik
# ---------------------------------------------------------------------------
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

def _enforce_scope(sql: str) -> bool:
    """Custom SQL tenant sınırında mı? İzolasyon İKİ meşru yoldan biriyle sağlanabilir:
      - products/orders vb. → store_id = ANY(:store_ids)
      - stores tablosu → s.user_id = :user_id (store'un sahibi; stores'ta store_id olmaz)
    İkisi de yoksa sorgu tüm tabloyu tarar = tenant izolasyon ihlali → reddet.
    Template'ler bu kontrolden muaftır; yalnızca LLM'in ürettiği custom SQL'e uygulanır."""
    return (":store_ids" in sql) or (":user_id" in sql)


def _ensure_uuid_cast(sql: str) -> str:
    sql = re.sub(
        r"ANY\s*\(\s*:store_ids\s*\)",
        "ANY(CAST(:store_ids AS uuid[]))",
        sql, flags=re.IGNORECASE,
    )
    sql = re.sub(
        r"::text\s*(=\s*ANY\s*\(CAST\(:store_ids AS uuid\[\]\)\))",
        r" \1", sql, flags=re.IGNORECASE,
    )
    return sql


def _ensure_limit(sql: str) -> str:
    if re.search(r"\bLIMIT\b", sql, re.IGNORECASE):
        return sql
    return sql.rstrip().rstrip(";") + " LIMIT 50"


# ---------------------------------------------------------------------------
# Şablon eşleştirici
# ---------------------------------------------------------------------------

_TEMPLATE_OPTIONS = "\n".join(
    f'  - {k}: {t["description"]}' for k, t in _TEMPLATES.items()
)

_ROUTER_PREAMBLE = f"""

ÖNCE ŞABLON KARARI:
Aşağıdaki hazır (test edilmiş) şablonlardan biri soruya TAM uyuyorsa onu seç.
Şablonlar MAĞAZA GENELİ veri içindir — soru belirli tek bir ürünü/markayı
adlandırıyorsa (tek ürünün fiyatı/stoğu/yorumu gibi) şablon UYMAZ, custom SQL yaz.

HAZIR ŞABLONLAR:
{_TEMPLATE_OPTIONS}

ÇIKTI:
- Uygun şablon varsa SADECE: {{"template": "<anahtar>"}}
- Uymuyorsa veya belirli ürün/marka adlandırılıyorsa custom SQL:
  {{"sql": "SELECT ...", "description": "...", "model_tier": "mini"}}
"""


def _route_and_generate(question: str, key: str) -> tuple[str, str, str, str | None]:
    """Tek LLM çağrısı: ya hazır şablonu seçer ya da custom SQL üretir.
    Keyword/marka listesi YOK — her ürün/markayla ölçeklenir.
    Döner: (sql, description, model_tier, template_key|None)."""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key, timeout=12)
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT + _ROUTER_PREAMBLE},
                {"role": "user", "content": question},
            ],
            temperature=0,
            max_tokens=500,
            response_format={"type": "json_object"},
        )
        raw = (completion.choices[0].message.content or "").strip()
        if not raw:
            return "", "LLM boş döndü.", "mini", None
        parsed = json.loads(raw)
        tkey = (parsed.get("template") or "").strip()
        if tkey and tkey in _TEMPLATES:
            t = _TEMPLATES[tkey]
            print(f"[NL_TO_SQL] Şablon seçildi (LLM): {t['description']}")
            return t["sql"].strip(), t["description"], "mini", tkey
        return (
            (parsed.get("sql") or "").strip(),
            parsed.get("description") or "",
            parsed.get("model_tier") or "mini",
            None,
        )
    except Exception as exc:
        print(f"[NL_TO_SQL] route_and_generate failed: {exc}")
        return "", f"LLM hatası: {exc}", "mini", None


# ---------------------------------------------------------------------------
# SQL doğrulayıcı — information_schema ile gerçek kolon kontrolü
# ---------------------------------------------------------------------------
def _validate_sql(sql: str) -> list[str]:
    """
    SQL'deki tablo ve kolon isimlerini information_schema ile kontrol eder.
    Hata listesi döner — boşsa SQL geçerli.
    """
    errors = []
    try:
        from app.core.database import SessionLocal

        # SQL'deki tablo isimlerini çıkar
        used_tables = re.findall(
            r"\bFROM\s+(\w+)|\bJOIN\s+(\w+)", sql, re.IGNORECASE
        )
        used_tables = [t for pair in used_tables for t in pair if t]

        with SessionLocal() as session:
            for table in used_tables:
                if table.lower() in ("stores", "products", "orders"):
                    continue  # Temel tablolar her zaman var

                # Tablo var mı?
                result = session.execute(text("""
                    SELECT COUNT(*) FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = :tname
                """), {"tname": table.lower()})
                if result.scalar() == 0:
                    errors.append(f"Tablo yok: {table}")
                    continue

                # Kolonları kontrol et — SQL'de bu tablodan kullanılan kolonları bul
                schema_cols = _SCHEMA_JSON.get(table.lower(), {}).get("NOT_AVAILABLE", [])
                for forbidden_col in schema_cols:
                    if re.search(rf"\b{re.escape(forbidden_col)}\b", sql, re.IGNORECASE):
                        errors.append(f"{table}.{forbidden_col} kolonu mevcut değil")

    except Exception as exc:
        print(f"[VALIDATE_SQL] kontrol atlandı: {exc}")

    return errors


# ---------------------------------------------------------------------------
# Ana fonksiyon
# ---------------------------------------------------------------------------
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

    # 1) Şablon eşleştir — varsa LLM'e gitme
    # 1) Tek LLM çağrısı — ya hazır şablonu seçer ya da custom SQL üretir.
    #    Keyword/marka listesi YOK; her ürün/markayla ölçeklenir.
    sql, description, model_tier, used_template = _route_and_generate(q, key)
    if not sql:
        return _empty(description)

    # 2) Sadece custom SQL doğrulanır (şablonlar zaten test edilmiş).
    if not used_template:
        errors = _validate_sql(sql)
        if errors:
            print(f"[NL_TO_SQL] Doğrulama hatası: {errors} — LLM'e geri gönderiliyor")
            error_msg = f"Önceki SQL'de hata: {', '.join(errors)}. Düzelt."
            sql, description, model_tier = _llm_generate_sql(
                f"{q}\n\nNOT: {error_msg}", key
            )
            if not sql:
                return _empty(description)

    # 4) Güvenlik
    if not _is_safe(sql):
        return _empty("Güvenlik: sadece SELECT sorgularına izin verilir.")

    # Tenant scope zorlaması: SADECE custom SQL'e uygulanır (template'ler zaten güvenli).
    # store_ids filtresi yoksa sorgu tüm tabloyu tarar = tenant sızıntısı → reddet.
    if not used_template and not _enforce_scope(sql):
        print(f"[NL_TO_SQL] GÜVENLİK: custom SQL'de :store_ids scope yok, reddedildi.\nSQL: {sql}")
        return _empty("Güvenlik: kapsam (store_ids) filtresi eksik, sorgu reddedildi.")

    # 5) UUID cast + LIMIT garantisi
    sql = _ensure_uuid_cast(sql)
    sql = _ensure_limit(sql)

    params: dict[str, Any] = {
        "store_ids": list(store_ids or []),
        "user_id": int(user_id),
    }

    # 6) Çalıştır — execution hatası olursa custom SQL'i bir kez LLM'e düzelttir
    from app.core.database import SessionLocal
    rows = None
    for attempt in range(2):
        try:
            with SessionLocal() as session:
                result = session.execute(text(sql), params)
                cols = list(result.keys())
                rows = [dict(zip(cols, row)) for row in result.fetchall()]
            break
        except Exception as exc:
            print(f"[NL_TO_SQL] SQL failed (deneme {attempt+1}): {exc}")
            # Şablonlar test edilmiştir; yalnızca custom SQL'i ve yalnızca ilk denemede düzelt
            if attempt == 0 and not used_template:
                fix_sql, fix_desc, fix_tier = _llm_generate_sql(
                    f"{q}\n\nÖnceki SQL şu PostgreSQL hatasını verdi:\n{exc}\n"
                    f"Hatalı SQL:\n{sql}\n"
                    f"Bu hatayı DÜZELT. Kural: GROUP BY kullanıyorsan SELECT'teki "
                    f"aggregate olmayan TÜM kolonları GROUP BY'a ekle; tek kayıt için "
                    f"GROUP BY'a gerek yoksa hiç kullanma.", key
                )
                if fix_sql and _is_safe(fix_sql) and _enforce_scope(fix_sql):
                    sql = _ensure_limit(_ensure_uuid_cast(fix_sql))
                    description = fix_desc or description
                    model_tier = fix_tier or model_tier
                    continue
            return _empty(f"Sorgu hatası: {exc}")

    if rows is None:
        return _empty("Sorgu çalıştırılamadı.")

    if not rows:
        return {
            "rows": [], "formatted": "(Bu sorgu için kayıt bulunamadı.)",
            "sql": sql, "description": description,
            "model_tier": model_tier, "row_count": 0,
            "error": None, "is_error": False,
        }

    return {
        "rows": rows,
        "formatted": _format_rows(rows, list(rows[0].keys())),
        "sql": sql, "description": description,
        "model_tier": model_tier, "row_count": len(rows),
        "error": None, "is_error": False,
    }


def _llm_generate_sql(question: str, key: str) -> tuple[str, str, str]:
    """LLM'den SQL üret. (sql, description, model_tier) döner."""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key, timeout=12)
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": question},
            ],
            temperature=0,
            max_tokens=500,
            response_format={"type": "json_object"},
        )
        raw = (completion.choices[0].message.content or "").strip()
        if not raw:
            return "", "LLM boş döndü.", "mini"
        parsed = json.loads(raw)
        return (
            (parsed.get("sql") or "").strip(),
            parsed.get("description") or "",
            parsed.get("model_tier") or "mini",
        )
    except Exception as exc:
        print(f"[NL_TO_SQL] LLM failed: {exc}")
        return "", f"LLM hatası: {exc}", "mini"


def _format_rows(rows: list[dict], cols: list[str]) -> str:
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
        "rows": [], "formatted": "",
        "sql": "", "description": reason,
        "model_tier": "mini", "row_count": 0,
        "error": reason, "is_error": True,
    }