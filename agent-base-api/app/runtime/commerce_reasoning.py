from __future__ import annotations

from typing import Any


class CommerceReasoning:
    """Domain-oriented commerce reasoning snippets for assistant narratives."""

    def _category_family(self, category: str, title: str) -> str:
        text = f"{category} {title}".strip().lower()
        if any(k in text for k in ("yiyecek", "icecek", "gida", "kahve", "pizza", "burger", "doner", "siparis")):
            return "food"
        if any(k in text for k in ("elektronik", "kulaklik", "telefon", "tablet", "laptop", "monitor")):
            return "electronics"
        if any(k in text for k in ("moda", "giyim", "ayakkabi", "tekstil")):
            return "fashion"
        if any(k in text for k in ("kozmetik", "bakim", "parfum")):
            return "beauty"
        return "general"

    def _delivery_expectation_note(self, family: str) -> str:
        if family == "food":
            return "Yiyecek kategorisinde teslimat hizi ve sicaklik algisi, tekrar satin alma davranisini diger kategorilere gore daha hizli etkiler."
        if family == "electronics":
            return "Elektronikte teslimat hizindan cok urun guveni ve yorum guvenirligi donusumu belirler."
        if family == "fashion":
            return "Moda kategorisinde beden uyumu ve iade kolayligi algisi, sepet kararini dogrudan etkileyebilir."
        if family == "beauty":
            return "Kozmetikte yorum guveni ve urun sonucu beklentisi, fiyat indiriminden daha kalici etki yaratabilir."
        return "Bu kategoride beklenti-performans dengesi bozuldugunda donusumde gecikmeli ama kalici etki olusabilir."

    def build(
        self,
        *,
        context: dict[str, Any],
        trend_pct: float,
        conversion_delta: float,
        return_rate: float,
        delivery_hit: bool,
        negative_reviews_up: bool,
        risk_trajectory: str,
    ) -> dict[str, Any]:
        product = dict(context.get("product_item") or {})
        overview = dict(context.get("product_overview") or {})
        insights = list(context.get("product_insights") or [])
        history = list(context.get("product_history") or [])
        reviews = list(context.get("product_reviews") or [])

        category = str(product.get("category") or overview.get("category") or "")
        title = str(product.get("name") or product.get("title") or "")
        family = self._category_family(category, title)
        insight_texts = [str(x.get("text") or "").lower() for x in insights if isinstance(x, dict)]
        review_texts = [str(x.get("comment") or "").lower() for x in reviews if isinstance(x, dict)]
        history_text = " ".join(str(x).lower() for x in history)

        domain_insights: list[str] = [self._delivery_expectation_note(family)]
        campaign_intelligence: list[str] = []
        segment_awareness: list[str] = []

        if any("fiyat" in t or "pahali" in t or "indirim" in t for t in review_texts + insight_texts):
            domain_insights.append("Fiyat hassasiyeti yukselen segmentlerde kisa vadeli indirim, uzun vadede algi asinmasi yaratabilir.")

        if any("kampanya" in t for t in insight_texts) or "campaign" in history_text:
            campaign_intelligence.append("Ardisik agresif kampanyalar donusumde kisa spike sonrasi hizli normallesme ve yorgunluk etkisi uretebilir.")
        if ("campaign" in history_text or "kampanya" in history_text) and trend_pct < 0:
            campaign_intelligence.append("Kampanya sonrasi ivmenin geri cekilmesi, remarketing baskisinin dogru segmentlenmedigine isaret ediyor olabilir.")

        if family == "food":
            segment_awareness.append("Hiz odakli musterilerde gec teslimat sinyali tekrar siparis kaybini hizlandirabilir.")
        elif family == "electronics":
            segment_awareness.append("Kararsiz alici segmentinde yorum guveni zayiflarsa sepetten cikis orani belirgin artabilir.")
        elif family == "fashion":
            segment_awareness.append("Beden/uyum belirsizligi olan segmentte iade riski donusumu baskilayabilir.")
        else:
            segment_awareness.append("Yeni musteri segmentinde yorum tonu bozulursa guven bariyeri hizla yukselebilir.")

        if any("aksam" in t for t in insight_texts):
            domain_insights.append("Aksam saatlerindeki performans artisi, niyetin yuksek oldugu pencereyi isaret eder; mesaj tonu o saate gore optimize edilebilir.")
        if any("hafta sonu" in t for t in insight_texts):
            domain_insights.append("Hafta sonu dalgalanmasi kategori ritmine bagli olabilir; stok ve kampanya yogunlugu birlikte planlanmali.")

        business_language = ""
        if risk_trajectory == "gucleniyor" and (delivery_hit or negative_reviews_up):
            business_language = "Bu tip sinyaller genelde once yorum guvenini, hemen ardindan tekrar satin alma ritmini etkileyebiliyor."
        elif conversion_delta < 0 and return_rate >= 4.5:
            business_language = "Donusum erozyonu ile iade baskisi ayni anda goruluyorsa marj kaybi kademeli sekilde derinlesebilir."
        elif trend_pct > 0 and conversion_delta >= 0:
            business_language = "Bu tablo kontrollu buyume penceresine isaret ediyor; fazla indirim yerine guven odakli mesaj daha saglikli olur."
        else:
            business_language = "Veri sadece ne oldugunu degil, musteri beklentisinin nasil degistigini de gosteriyor."

        return {
            "category_family": family,
            "category_label": category or "genel",
            "domain_insights": domain_insights[:4],
            "campaign_intelligence": campaign_intelligence[:3],
            "segment_awareness": segment_awareness[:2],
            "business_language": business_language,
        }

