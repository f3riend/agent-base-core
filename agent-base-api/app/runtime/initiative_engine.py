from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


class InitiativeEngine:
    """Detects proactive copilot interventions from runtime context."""

    _NEGATIVE_KEYWORDS = ("gec", "gecik", "sikayet", "soguk", "kotu", "iade", "iptal")
    _SEVERITY_WEIGHT = {
        "critical": 3.0,
        "warning": 2.4,
        "opportunity": 2.1,
        "insight": 1.8,
        "info": 1.4,
    }
    _COOLDOWN_MINUTES = 30

    def _initiative_history(self, entity_memory: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        for row in entity_memory:
            if str(row.get("kind") or "") != "initiative":
                continue
            code = str(row.get("code") or "").strip()
            if code:
                out.setdefault(code, []).append(dict(row))
        return out

    def _parse_iso(self, raw: str) -> datetime | None:
        txt = str(raw or "").strip()
        if not txt:
            return None
        if txt.endswith("Z"):
            txt = txt[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(txt)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            return None

    def _cooldown_ok(self, rows: list[dict[str, Any]], now: datetime) -> bool:
        latest: datetime | None = None
        for row in rows:
            dt = self._parse_iso(str(row.get("timestamp") or ""))
            if dt is None:
                continue
            if latest is None or dt > latest:
                latest = dt
        if latest is None:
            return True
        return (now - latest) >= timedelta(minutes=self._COOLDOWN_MINUTES)

    def evaluate(
        self,
        *,
        context: dict[str, Any],
        entity_memory: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        history = self._initiative_history(entity_memory)
        now = datetime.now(timezone.utc)
        product_id = str(context.get("product_id") or "")
        trend_pct = float(context.get("product_trend_pct") or 0.0)
        overview = dict(context.get("product_overview") or {})
        reviews = list(context.get("product_reviews") or [])
        insights = list(context.get("product_insights") or [])
        product_item = dict(context.get("product_item") or {})

        return_rate = float(overview.get("returnRate") or 0.0)
        conversion_delta = float(overview.get("conversionDelta") or product_item.get("conversionDelta") or 0.0)
        stock_level = int(product_item.get("stock") or product_item.get("stockLevel") or 0)

        review_texts = [str(x.get("comment") or "").lower() for x in reviews if isinstance(x, dict)]
        insight_texts = [str(x.get("text") or "").lower() for x in insights if isinstance(x, dict)]
        negative_reviews = sum(1 for txt in review_texts if any(k in txt for k in self._NEGATIVE_KEYWORDS))
        delivery_signal = any(("kargo" in txt or "teslimat" in txt or "kurye" in txt) for txt in (review_texts + insight_texts))
        evening_signal = any("aksam" in txt for txt in insight_texts)

        initiatives: list[dict[str, Any]] = []

        def add(
            code: str,
            severity: str,
            message: str,
            suggested_actions: list[str],
            quick_actions: list[dict[str, str]],
            *,
            impact: float,
            confidence: float,
            follow_up: str,
        ) -> None:
            rows = history.get(code) or []
            if not self._cooldown_ok(rows, now):
                return
            repeat_count = len(rows)
            severity_weight = self._SEVERITY_WEIGHT.get(severity, 1.0)
            priority = severity_weight + impact + confidence + min(repeat_count, 3) * 0.2
            if priority < 2.6:
                return
            initiatives.append(
                {
                    "code": code,
                    "severity": severity,
                    "message": message,
                    "impact": round(impact, 2),
                    "confidence": round(confidence, 2),
                    "repeat_count": repeat_count,
                    "priority": round(priority, 2),
                    "suggested_actions": suggested_actions,
                    "quick_actions": quick_actions,
                    "follow_up_question": follow_up,
                    "related_entities": ([{"type": "product", "id": product_id}] if product_id else []),
                }
            )

        if negative_reviews >= 2 and delivery_signal:
            add(
                code="delivery_complaint_rise",
                severity="warning",
                message="Bu arada son geri bildirimlerde teslimat kaynakli sikayetlerin belirgin sekilde arttigi dikkat cekiyor.",
                suggested_actions=[
                    "Yorumlari kategori bazli ayir",
                    "Teslimat kaynakli sorunlar icin hizli aksiyon plani olustur",
                ],
                quick_actions=[
                    {"label": "Yorumlari Ayir", "action": "analyze_reviews", "command": "Teslimat kaynakli yorumlari kategori bazli ayir"},
                ],
                impact=0.9,
                confidence=0.84,
                follow_up="Istersen teslimat sikayetlerini once hiz, sonra urun kalitesi basliginda ayirabilirim.",
            )

        if trend_pct <= -6:
            sales_msg = (
                "Son 48 saatte satis trendinde belirgin bir sicrama ile gerileme var; bu sinyal kritik seviyede."
                if trend_pct <= -12
                else "Ilk sinyaller satis trendinde dususun netlesmeye basladigini gosteriyor; bu durum tekrar siparis davranisini etkilemis olabilir."
            )
            add(
                code="sales_drop_signal",
                severity="critical" if trend_pct <= -12 else "warning",
                message=sales_msg,
                suggested_actions=[
                    "Satis dususu neden analizi yap",
                    "Kampanya stratejisini guncelle",
                ],
                quick_actions=[
                    {"label": "Neden Analizi", "action": "analyze_product", "command": "Satis dususunun ana nedenlerini analiz et"},
                    {"label": "Kampanya Oner", "action": "create_campaign", "command": "Satis dususunu dengelemek icin kampanya oner"},
                ],
                impact=1.0 if trend_pct <= -12 else 0.86,
                confidence=0.78,
                follow_up="Istersen dususu tetikleyen basliklari etkisine gore siralayip once hangi adima girecegimizi netlestirebiliriz.",
            )

        if conversion_delta < -0.05:
            add(
                code="conversion_drop",
                severity="warning",
                message="Dikkat ceken nokta, donusum oraninda zayiflama gorulmesi; urun sayfasi mesaji ile yorum tonu arasinda uyumsuzluk olabilir.",
                suggested_actions=[
                    "Donusum dususu icin icerik testi baslat",
                    "Sepet oncesi mesajlari optimize et",
                ],
                quick_actions=[
                    {"label": "Icerik Testi", "action": "generate_caption", "command": "Donusum icin iki farkli mesaj tonu olustur"},
                ],
                impact=0.78,
                confidence=0.69,
                follow_up="Istersen once urun sayfasi mesaji ile yorum tonunu uyumlu hale getirecek hizli bir test akisi cikarabilirim.",
            )

        if return_rate >= 5:
            add(
                code="return_rate_rise",
                severity="warning",
                message="Derine indikce iade oraninin ortalamanin uzerine ciktigi goruluyor; bu tablo kalite algisi veya beklenti uyumsuzlugu riski olusturuyor gibi.",
                suggested_actions=[
                    "Iade nedenlerini ozetle",
                    "Urun beklenti metinlerini netlestir",
                ],
                quick_actions=[
                    {"label": "Iade Analizi", "action": "summarize_operational_insights", "command": "Iade kaynakli sorunlari ozetle"},
                ],
                impact=0.82,
                confidence=0.74,
                follow_up="Istersen iade kaynaklarini kategori bazinda toplayip en hizli duzeltilebilir alani birlikte secebiliriz.",
            )

        if stock_level and stock_level <= 10:
            add(
                code="stock_low_signal",
                severity="info",
                message="Bu arada stok seviyesinin kritik sinira yaklasmasi kampanya zamanlamasini yeniden dusunmeyi gerektirebilir.",
                suggested_actions=[
                    "Stok duyarliligina gore kampanya yogunlugunu ayarla",
                ],
                quick_actions=[
                    {"label": "Kampanya Ayarla", "action": "optimize_campaign", "command": "Stok seviyesine gore kampanya yogunlugunu optimize et"},
                ],
                impact=0.64,
                confidence=0.66,
                follow_up="Istersen stok hassasiyetine gore kampanya yogunlugunu kontrollu bir plana cekebilirim.",
            )

        if trend_pct >= 8 or evening_signal:
            add(
                code="high_performance_window",
                severity="opportunity",
                message="Ote yandan aksam saatlerinde performans penceresi gucleniyor gibi gorunuyor; bu zamani daha verimli kullanabiliriz.",
                suggested_actions=[
                    "Aksam saatine ozel mini kampanya hazirla",
                    "Yuksek performans penceresinde icerik yogunlugunu artir",
                ],
                quick_actions=[
                    {"label": "Aksam Kampanyasi", "action": "create_campaign", "command": "Aksam saatlerine ozel mini kampanya olustur"},
                ],
                impact=0.74,
                confidence=0.71,
                follow_up="Istersen yuksek performans penceresini daha iyi kullanmak icin aksam odakli mini bir plan cikarabilirim.",
            )

        initiatives.sort(key=lambda x: float(x.get("priority") or 0), reverse=True)
        risk_item = next((x for x in initiatives if str(x.get("severity") or "") in {"warning", "critical"}), None)
        opportunity_item = next((x for x in initiatives if str(x.get("severity") or "") == "opportunity"), None)

        selected: list[dict[str, Any]] = []
        if risk_item is not None:
            selected.append(risk_item)
        if opportunity_item is not None and opportunity_item is not risk_item:
            selected.append(opportunity_item)
        if not selected and initiatives:
            selected.append(initiatives[0])

        if selected:
            selected[0]["include_follow_up"] = True
        for item in selected[1:]:
            item["include_follow_up"] = False
            item["follow_up_question"] = ""
        return selected[:2]
