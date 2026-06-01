from __future__ import annotations

from typing import Any

from .commerce_reasoning import CommerceReasoning


class AssistantNarrativeComposer:
    """Rule-based narrative layer for conversational assistant responses."""

    def __init__(self) -> None:
        self.commerce = CommerceReasoning()

    _TOOL_RUNNING = {
        "analyze_product": "Satis verilerindeki hareketi anlamlandiriyorum.",
        "analyze_reviews": "Yorumlarda tekrar eden memnuniyetsizlik basliklarina bakiyorum.",
        "detect_complaint_clusters": "Sikayetleri tema bazinda gruplandirip neyin one ciktigini inceliyorum.",
        "generate_mitigation_plan": "Sorunlari hafifletecek pratik adimlari netlestiriyorum.",
        "generate_strategy": "Urunun ritmine uygun kampanya yonunu sekillendiriyorum.",
        "generate_caption": "Mesajin tonu ve donusum amacina uygun metin taslaklari olusturuyorum.",
        "generate_banner_copy": "Banner metnini ilgi cekecek sekilde rafine ediyorum.",
        "generate_banner_visual": "Gorsel yonun performansi nasil etkileyebilecegini optimize ediyorum.",
        "generate_image": "Gorsel alternatifi hazirlayip uygunlugu kontrol ediyorum.",
        "create_approval": "Onay gerektiren adimlari netlestiriyorum.",
    }

    _TOOL_DONE = {
        "analyze_product": "Satis ve gelir sinyalleri birlikte bakildiginda dikkat ceken bir desen gorunuyor.",
        "analyze_reviews": "Yorumlarda benzer sikayetlerin belirli basliklarda toplandigi goruluyor.",
        "detect_complaint_clusters": "Sikayetler ozellikle birkac konuda yogunlasiyor gibi duruyor.",
        "generate_mitigation_plan": "Sorunu kisa vadede hafifletecek adimlar netlesmeye basladi.",
        "generate_strategy": "Kampanya yonu urunun mevcut ritmine daha uygun hale geldi.",
        "generate_caption": "Metin onerileri urunun sorununu daha ikna edici anlatacak seviyeye geldi.",
        "generate_banner_copy": "Banner mesajinin etkisini artiracak bir dil on plana cikti.",
        "generate_banner_visual": "Gorsel yonu destekleyen daha tutarli bir cizgi olustu.",
        "generate_image": "Gorsel secenekleri urunun algisini guclendirecek yonde gorunuyor.",
        "create_approval": "Onay adimlari netlesmis durumda, karar sureci daha kontrollu ilerleyebilir.",
    }

    _NEGATIVE_KEYWORDS = (
        "gec",
        "gecik",
        "soguk",
        "sikayet",
        "iade",
        "kotu",
        "iptal",
    )
    _DELIVERY_KEYWORDS = ("kargo", "kurye", "teslimat", "gec", "gecik")

    def _risk_trajectory(
        self,
        *,
        trend_pct: float,
        conversion_delta: float,
        return_rate: float,
        delivery_hit: bool,
        negative_reviews_up: bool,
        insight_texts: list[str],
    ) -> str:
        if trend_pct < -5 or (negative_reviews_up and delivery_hit):
            return "gucleniyor"
        if trend_pct > 2 and conversion_delta >= 0:
            return "zayifliyor"
        if abs(trend_pct) <= 1.2 and abs(conversion_delta) <= 0.01 and return_rate < 4.5:
            return "stabil kaliyor"
        if any("yeniden" in t for t in insight_texts):
            return "yeniden ortaya cikiyor"
        return "stabil kaliyor"

    def _early_signal_note(self, confidence: float, trajectory: str, delivery_hit: bool, negative_reviews_up: bool) -> str:
        if confidence < 0.68:
            if delivery_hit:
                return "Ilk sinyaller memnuniyet tarafinda yavas bir asinmaya isaret ediyor olabilir."
            return "Ilk sinyaller performans tarafinda dikkat edilmesi gereken hafif bir kaymaya isaret ediyor olabilir."
        if trajectory == "gucleniyor" and negative_reviews_up:
            return "Erken sinyaller riskin su an zayiflamadigini, aksine adim adim guclendigini gosteriyor."
        if trajectory == "yeniden ortaya cikiyor":
            return "Daha once zayiflayan riskin yeniden gorunur hale geldigi bir evreye giriliyor gibi duruyor."
        return "Erken sinyaller su an kontrollu bir izlemenin yeterli olabilecegini gosteriyor."

    def _predictive_outlook(
        self,
        *,
        confidence: float,
        trajectory: str,
        sales_down: bool,
        conversion_delta: float,
        return_rate: float,
    ) -> str:
        if trajectory == "gucleniyor":
            if confidence >= 0.82:
                return (
                    "Bu egilim bu sekilde devam ederse tekrar siparis oraninda daha belirgin bir dusus, "
                    "donusumde ise kademeli bir kayip gormemiz olasi."
                )
            return (
                "Bu trend korunursa tekrar siparis ve donusum tarafinda asamali bir baski olusabilir."
            )
        if trajectory == "yeniden ortaya cikiyor":
            return "Risk yeniden beliriyor; erken mudahale edilmezse iade maliyeti ve memnuniyet baskisi tekrar artabilir."
        if trajectory == "zayifliyor":
            return "Bu toparlanma korunursa donusum ve sepet buyuklugunde olcumlu bir iyilesme beklenebilir."
        if sales_down or conversion_delta < 0 or return_rate >= 4.5:
            return "Kisa vadede buyuk kirilma beklenmese de is etkisi birikimli sekilde buyuyebilir."
        return "Gorunur bir bozulma beklenmiyor; yine de ritim degisimlerini yakindan takip etmek faydali olur."

    def _response_discipline(
        self,
        *,
        risk_count: int,
        consequence_count: int,
        confidence: float,
        risk_trajectory: str,
        intent: str,
        explicit_detail: bool = False,
    ) -> tuple[str, int]:
        if explicit_detail:
            return "detailed", 115
        if risk_trajectory == "gucleniyor" and (risk_count >= 2 or consequence_count >= 2 or confidence >= 0.82):
            return "warning", 92
        if risk_count == 0 and consequence_count == 0 and risk_trajectory in {"stabil kaliyor", "zayifliyor"}:
            return "brief", 42
        if risk_count <= 1 and consequence_count <= 1 and confidence < 0.78 and intent in {"general_analysis", "analyze_reviews"}:
            return "brief", 46
        return "brief", 58

    def _infer_expertise_level(
        self,
        *,
        intent: str,
        mode: str,
        message: str,
        risk_trajectory: str,
        confidence: float,
        detected_risks: list[str],
        history: list[dict[str, Any]],
    ) -> tuple[str, str]:
        txt = (message or "").strip().lower()
        deep_keywords = ("detay", "kök neden", "segment", "hipotez", "senaryo", "stratej", "plan", "uzun vad")
        simple_keywords = ("ne oldu", "durum", "kisa", "ozet", "tek cumle", "kisaca")

        user_deep_count = 0
        for row in history[-6:]:
            if str(row.get("role") or "").lower() != "user":
                continue
            c = str(row.get("content") or "").lower()
            if any(k in c for k in deep_keywords):
                user_deep_count += 1

        if mode == "strateji" or intent in {"create_campaign", "optimize_campaign"} and any(k in txt for k in ("senaryo", "strateji", "plan")):
            return "strategic", "Kullanici niyeti stratejik derinlik gerektiriyor."
        if any(k in txt for k in deep_keywords) or user_deep_count >= 2:
            return "expert", "Kullanici derin analiz tonu talep ediyor."
        if risk_trajectory == "gucleniyor" and (len(detected_risks) >= 2 or confidence >= 0.84):
            return "expert", "Risk sinyali yuksek; daha uzman yorum gerekli."
        if any(k in txt for k in simple_keywords) or (len(txt) <= 38 and len(detected_risks) <= 1):
            return "casual", "Soru kisa ve sade; hafif anlatim daha uygun."
        return "operational", "Operasyon seviyesinde dengeli uzmanlik uygun."

    def _confidence_phrase(self, confidence: float) -> str:
        if confidence >= 0.88:
            return "Bu sinyal su an oldukca tutarli gorunuyor."
        if confidence >= 0.76:
            return "Bu yorum orta-yuksek guven seviyesine dayaniyor."
        if confidence >= 0.64:
            return "Bu noktada ilk sinyaller yon veriyor, yine de izlemeye devam etmek gerekir."
        return "Bu yorum daha cok erken sinyallere dayaniyor; kesin yargidan kaciniyorum."

    def _build_evidence_signals(
        self,
        *,
        review_texts: list[str],
        insight_texts: list[str],
        history: list[dict[str, Any]],
        tool_states: list[dict[str, Any]],
    ) -> list[str]:
        signals: list[str] = []
        if review_texts:
            signals.append("Son yorumlarda tekrar eden geri bildirim kaliplari one cikiyor.")
        if any("hafta sonu" in t for t in insight_texts):
            signals.append("Ozellikle hafta sonundan sonra ritim degisimi dikkat cekiyor.")
        if any("aksam" in t for t in insight_texts):
            signals.append("Aksam penceresindeki davranis farki surekli tekrar ediyor.")
        if history and any("teslimat" in str(x.get("content") or "").lower() or "kargo" in str(x.get("content") or "").lower() for x in history):
            signals.append("Konusma gecmisinde teslimat ekseni tekrar ediyor.")
        if tool_states and any(str(t.get("status") or "") == "completed" for t in tool_states):
            signals.append("Tamamlanan analiz adimlari benzer yone isaret ediyor.")
        return signals[:4]

    def _trust_signal_strength(self, confidence: float, evidence_count: int, risk_trajectory: str) -> str:
        if confidence >= 0.86 and evidence_count >= 3 and risk_trajectory in {"gucleniyor", "yeniden ortaya cikiyor"}:
            return "guclu"
        if confidence >= 0.72 and evidence_count >= 2:
            return "orta"
        return "erken"

    def _entity_refs(self, context: dict[str, Any]) -> list[dict[str, str]]:
        refs: list[dict[str, str]] = []
        product_id = str(context.get("product_id") or "")
        store_id = str(context.get("store_id") or "")
        order_id = str(context.get("order_id") or "")
        if product_id:
            refs.append({"type": "product", "id": product_id})
        if store_id:
            refs.append({"type": "store", "id": store_id})
        if order_id:
            refs.append({"type": "order", "id": order_id})
        return refs

    def _quick_actions(
        self,
        intent: str,
        delivery_hit: bool,
        negative_reviews_up: bool,
        sales_down: bool,
        *,
        domain: str,
        campaign_allowed: bool,
    ) -> list[dict[str, str]]:
        actions: list[dict[str, str]] = []
        if negative_reviews_up:
            actions.append({"label": "Yorumlari Analiz Et", "action": "analyze_reviews", "command": "Bu urunun yorumlarini analiz et"})
        if campaign_allowed and intent in {"create_campaign", "optimize_campaign"}:
            actions.append({"label": "Kampanya Olustur", "action": "create_campaign", "command": "Bu urun icin kampanya olustur"})
        if delivery_hit:
            actions.append({"label": "Operasyon Ozeti Cikar", "action": "summarize_operational_insights", "command": "Teslimat sorunlarini operasyon bazinda ozetle"})
        if campaign_allowed and intent in {"create_campaign", "generate_banner"}:
            actions.append({"label": "Banner Olustur", "action": "generate_banner", "command": "Bu urun icin banner olustur"})
        if sales_down:
            actions.append({"label": "Metrik Dususunu Incele", "action": "analyze_product", "command": "Satis ve donusum dususunu ozetle"})
        if domain in {"analytics", "support", "general_chat", "strategy"}:
            actions = [x for x in actions if str(x.get("action") or "") not in {"create_campaign", "generate_banner"}]
        uniq: list[dict[str, str]] = []
        seen: set[str] = set()
        for row in actions:
            key = str(row.get("action") or "")
            if key and key not in seen:
                seen.add(key)
                uniq.append(row)
        return uniq[:2]

    def _tone_and_confidence(self, tool_states: list[dict[str, Any]], detected_risks: list[str], detected_opportunities: list[str]) -> tuple[str, float]:
        completed = sum(1 for t in tool_states if str(t.get("status") or "") == "completed")
        total = max(1, len(tool_states))
        base = 0.55 + 0.3 * (completed / total)
        if detected_risks:
            base += 0.06
        if detected_opportunities:
            base += 0.04
        confidence = round(min(0.96, max(0.42, base)), 2)
        tone = "warning" if detected_risks else "insight"
        if detected_risks and detected_opportunities:
            tone = "analysis"
        return tone, confidence

    def compose_thinking(self, tool: str) -> dict[str, Any]:
        return {
            "type": "thinking",
            "tone": "insight",
            "intent": "thinking",
            "confidence": 0.6,
            "message": self._TOOL_RUNNING.get(tool, "Operasyon adimini isliyorum."),
            "sections": [],
            "suggested_actions": [],
            "quick_replies": ["Devam et", "Detayi acikla"],
            "quick_actions": [],
            "related_entities": [],
        }

    def compose_tool_update(self, tool: str, status: str, result: dict[str, Any] | None = None) -> dict[str, Any]:
        message = self._TOOL_DONE.get(tool, "Operasyon adimi tamamlandi.")
        if status != "completed":
            message = self._TOOL_RUNNING.get(tool, "Operasyon adimi isleniyor.")
        sections: list[dict[str, Any]] = []
        if isinstance(result, dict) and result:
            highlights = [f"{k}: {v}" for k, v in list(result.items())[:3] if v not in (None, "", [], {})]
            if highlights:
                sections.append({"title": "Cikti Ozet", "items": highlights})
        return {
            "type": "tool_update",
            "tone": "insight" if status == "completed" else "analysis",
            "intent": tool,
            "confidence": 0.73 if status == "completed" else 0.62,
            "message": message,
            "sections": sections,
            "suggested_actions": [],
            "quick_replies": ["Bunu ac", "Devam et"],
            "quick_actions": [],
            "related_entities": [],
        }

    def compose_final(
        self,
        *,
        intent: str,
        context: dict[str, Any],
        tool_states: list[dict[str, Any]],
        pending_actions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        overview = dict(context.get("product_overview") or {})
        reviews = list(context.get("product_reviews") or [])
        insights = list(context.get("product_insights") or [])
        history = list(context.get("chat_history") or [])
        previous_ops = list(context.get("previous_operations") or [])
        product_history = list(context.get("product_history") or [])
        mode = str(context.get("mode") or "analiz").strip().lower()
        user_message = str(context.get("last_user_message") or "").strip()
        user_message_l = user_message.lower()
        sem = dict(context.get("operation_semantics") or {})
        domain = str(sem.get("domain") or "").strip().lower()
        trend_pct = float(context.get("product_trend_pct") or 0.0)
        return_rate = float(overview.get("returnRate") or 0.0)
        conversion_delta = float(overview.get("conversionDelta") or context.get("conversion_delta") or 0.0)

        review_texts = [str(x.get("comment") or "").lower() for x in reviews if isinstance(x, dict)]
        insight_texts = [str(x.get("text") or "").lower() for x in insights if isinstance(x, dict)]

        delivery_hit = any(
            any(k in text for k in self._DELIVERY_KEYWORDS) and any(nk in text for nk in self._NEGATIVE_KEYWORDS)
            for text in review_texts + insight_texts
        )
        negative_count = sum(1 for text in review_texts if any(k in text for k in self._NEGATIVE_KEYWORDS))
        ratings = [float(x.get("rating") or 0) for x in reviews if isinstance(x, dict)]
        low_rating_count = sum(1 for rating in ratings if rating and rating <= 3)
        negative_reviews_up = (negative_count + low_rating_count) >= 2
        sales_down = trend_pct < 0
        return_risk = return_rate >= 4.5

        detected_risks: list[str] = []
        detected_opportunities: list[str] = []

        if sales_down:
            detected_risks.append(f"Satis trendinde dusus var ({trend_pct:.1f}%).")
        if negative_reviews_up:
            detected_risks.append("Negatif yorum yogunlugu artmis gorunuyor.")
        if delivery_hit:
            detected_risks.append("Teslimat veya kargo kaynakli memnuniyetsizlik sinyali var.")
        if return_risk:
            detected_risks.append("Iade orani ortalamanin uzerinde.")

        if not sales_down:
            detected_opportunities.append("Satis trendi pozitif.")
        if intent in {"create_campaign", "optimize_campaign"} and domain in {"content_ops", "publishing", "scheduling"}:
            detected_opportunities.append("Kampanya adimi acikca talep edildi.")
        if any("aksam" in t for t in insight_texts):
            detected_opportunities.append("Aksam saatlerinde yuksek talep penceresi goruluyor.")
        if any("hafta sonu" in t for t in insight_texts):
            detected_opportunities.append("Hafta sonu davranisinda tekrar eden bir desen var.")

        tone, confidence = self._tone_and_confidence(tool_states, detected_risks, detected_opportunities)
        risk_trajectory = self._risk_trajectory(
            trend_pct=trend_pct,
            conversion_delta=conversion_delta,
            return_rate=return_rate,
            delivery_hit=delivery_hit,
            negative_reviews_up=negative_reviews_up,
            insight_texts=insight_texts,
        )

        faq_rows = list(context.get("product_faq") or [])
        support_rows = list(context.get("product_support_tickets") or [])
        metric_rows = list(context.get("product_metrics_daily") or [])

        if sales_down and negative_reviews_up and delivery_hit:
            analysis_summary = "Satis dususunu en cok teslimat kaynakli memnuniyetsizlik tetikliyor gibi gorunuyor."
        elif sales_down and negative_reviews_up:
            analysis_summary = "Satis dususu ile negatif yorum yogunlugu ayni donemde artmis gorunuyor."
        elif sales_down:
            analysis_summary = "Satis trendinde belirgin bir yavaslama var."
        elif support_rows:
            analysis_summary = "Destek kayitlarinda tekrar eden sorun basliklari gorunuyor."
        elif review_texts:
            analysis_summary = "Yorumlarda tekrar eden basliklar gorunuyor."
        elif metric_rows:
            analysis_summary = "Gunluk metriklerde hareket var."
        elif faq_rows:
            analysis_summary = "SSS tarafinda sik tekrar eden sorular var."
        else:
            analysis_summary = "Temel metrikler su an dengeli."

        reasoning_response = (
            "Ilk sinyaller yorum tarafinda teslimat ekseninin one ciktigini gosteriyor. "
            "Ozellikle tekrar eden gec teslimat ve urun sicakligi geri bildirimleri dikkat cekiyor. "
            f"Bu tabloya trend metriklerini ekledigimde {analysis_summary}"
        )

        alternative_hypothesis = (
            "Buna ek olarak fiyat degisimi veya urun sayfasindaki anlatim da etkili olmus olabilir."
            if sales_down
            else "Teslimat disinda urun metni ya da beklenti uyumsuzlugu da etkiliyor olabilir."
        )

        brief_request = any(k in user_message_l for k in ("kisaca", "kisa anlat", "ozetle"))
        explicit_detail = any(k in user_message_l for k in ("detay", "detayli", "uzun", "adim adim", "nedenini ac"))
        campaign_allowed = (
            intent in {"create_campaign", "generate_banner", "optimize_campaign"}
            or any(k in user_message_l for k in ("kampanya", "banner", "post", "reel", "hikaye", "icerik olustur"))
        )

        suggested_actions: list[str] = []
        if delivery_hit:
            suggested_actions.append("Kargo performans raporu hazirla")
        if negative_reviews_up:
            suggested_actions.append("Yorum analizi operasyonu baslat")
        if sales_down and campaign_allowed:
            suggested_actions.append("Hedefli kampanya stratejisi olustur")
        if sales_down and not campaign_allowed:
            suggested_actions.append("Satis dususunun teknik ve operasyonel nedenlerini ayristir")
        if not suggested_actions:
            suggested_actions.extend(
                [
                    "Urun performans ozetini guncelle",
                    "Destek surecini hizlandir",
                    "Iade nedenlerini analiz et",
                ]
            )
        if pending_actions:
            suggested_actions.append("Bekleyen onay adimlarini tamamla")

        recommendation_summary = "Once tekrar eden sorun basligina odakli tek bir adim atmak daha saglikli gorunuyor."

        continuity_notes: list[str] = []
        if history and any("teslimat" in str(x.get("content") or "").lower() or "kargo" in str(x.get("content") or "").lower() for x in history):
            continuity_notes.append("Gecen analizlerde teslimat odagini konusmustuk; benzer sinyaller yeniden gucleniyor.")
        if previous_ops:
            continuity_notes.append("Onceki operasyon kayitlari da benzer basliklarin tekrar ettigine isaret ediyor.")
        if product_history:
            continuity_notes.append("Urun gecmisinde benzer dalgalanma deseni tekrar ediyor gibi gorunuyor.")

        temporal_observation = ""
        if trend_pct < 0 and negative_reviews_up:
            temporal_observation = "Son birkac gun icinde negatif yorum yogunlugunun satis trendiyle birlikte yeniden artmaya basladigi goruluyor."
        elif trend_pct >= 0 and any("aksam" in t for t in insight_texts):
            temporal_observation = "Gecen haftaya gore aksam saatindeki performans penceresi yeniden gucleniyor gibi gorunuyor."
        elif trend_pct < 0:
            temporal_observation = "Son gunlerde dusus sinyali daha belirgin hale geliyor."

        business_impact_note = ""
        if trend_pct < 0:
            business_impact_note = "Bu gidis satislari baskilamaya devam edebilir."
        elif conversion_delta < 0:
            business_impact_note = "Bu tablo donusumde dususe yol acabilir."
        elif return_rate >= 4.5:
            business_impact_note = "Iade orani maliyeti artirabilir."
        business_consequences: list[str] = []
        if conversion_delta < 0:
            business_consequences.append("Donusum oraninda asamali zayiflama riski var.")
        if trend_pct < 0:
            business_consequences.append("Sepet buyuklugu ve tekrar siparis davranisinda asagi yonlu baski olusabilir.")
        if return_rate >= 4.5:
            business_consequences.append("Iade maliyeti ve operasyonel yuk artabilir.")
        if delivery_hit:
            business_consequences.append("Teslimat kaynakli memnuniyetsizlik artabilir.")
        early_signal_note = self._early_signal_note(confidence, risk_trajectory, delivery_hit, negative_reviews_up)
        predictive_outlook = self._predictive_outlook(
            confidence=confidence,
            trajectory=risk_trajectory,
            sales_down=sales_down,
            conversion_delta=conversion_delta,
            return_rate=return_rate,
        )
        commerce = self.commerce.build(
            context=context,
            trend_pct=trend_pct,
            conversion_delta=conversion_delta,
            return_rate=return_rate,
            delivery_hit=delivery_hit,
            negative_reviews_up=negative_reviews_up,
            risk_trajectory=risk_trajectory,
        )
        domain_insights = list(commerce.get("domain_insights") or [])
        campaign_intelligence = list(commerce.get("campaign_intelligence") or [])
        segment_awareness = list(commerce.get("segment_awareness") or [])
        business_language = str(commerce.get("business_language") or "").strip()
        response_mode, response_word_target = self._response_discipline(
            risk_count=len(detected_risks),
            consequence_count=len(business_consequences),
            confidence=confidence,
            risk_trajectory=risk_trajectory,
            intent=intent,
            explicit_detail=explicit_detail,
        )
        if domain in {"analytics", "support"}:
            response_mode, response_word_target = ("brief", 44)
            if explicit_detail:
                response_mode, response_word_target = ("detailed", 90)
        if brief_request:
            response_mode, response_word_target = ("brief", 32)
        expertise_level, expertise_reason = self._infer_expertise_level(
            intent=intent,
            mode=mode,
            message=user_message,
            risk_trajectory=risk_trajectory,
            confidence=confidence,
            detected_risks=detected_risks,
            history=history,
        )
        if expertise_level == "casual":
            response_mode, response_word_target = "brief", 55
        elif expertise_level == "strategic" and response_mode == "brief":
            response_mode, response_word_target = "detailed", 155
        confidence_phrase = self._confidence_phrase(confidence)
        evidence_signals = self._build_evidence_signals(
            review_texts=review_texts,
            insight_texts=insight_texts,
            history=history,
            tool_states=tool_states,
        )
        trust_signal_strength = self._trust_signal_strength(confidence, len(evidence_signals), risk_trajectory)
        reasoning_transparency = (
            f"Neden boyle dusunuyorum: {evidence_signals[0]} {confidence_phrase}"
            if evidence_signals
            else f"Neden boyle dusunuyorum: Netlesme asamasinda sinyalleri birlikte okuyorum. {confidence_phrase}"
        )

        sections: list[dict[str, Any]] = []
        if explicit_detail:
            sections.append({"title": "Ana Neden", "content": analysis_summary})
            if detected_risks:
                sections.append({"title": "Riskler", "items": detected_risks[:2]})
            if evidence_signals:
                sections.append({"title": "Dayanak", "items": evidence_signals[:2]})

        asked_delivery = any(k in user_message_l for k in ("teslimat", "kargo", "kurye"))
        delivery_data_present = any(any(k in text for k in self._DELIVERY_KEYWORDS) for text in (review_texts + insight_texts))
        if asked_delivery and not delivery_data_present:
            analysis_summary = "Elimde teslimat kaynakli guclu veri yok."

        evidence = evidence_signals[0] if evidence_signals else "Bu konuda veri sinirli."
        impact_line = business_impact_note or (business_consequences[0] if business_consequences else "Etki su an sinirli.")
        if confidence < 0.66 and "olabilir" not in impact_line:
            impact_line = impact_line.rstrip(".") + " olabilir."

        first_action = recommendation_summary
        if support_rows:
            first_action = "Acil destek kayitlarini issue tipine gore ayir."
        elif reviews:
            first_action = "Son yorumlari tekrar eden sorun basligina gore grupla."
        elif metric_rows:
            first_action = "Son 7 gun metrik dususunu kanal bazinda dogrula."

        if domain in {"analytics", "support"}:
            problem_line = analysis_summary
            if confidence < 0.72 and "olabilir" not in problem_line and "veri yok" not in problem_line:
                problem_line = problem_line.rstrip(".") + " olabilir."
            if brief_request:
                message = (
                    f"Problem: {problem_line} "
                    f"Kanit: {evidence} "
                    f"Ilk aksiyon: {first_action}"
                )
            else:
                message = (
                    f"Problem: {problem_line} "
                    f"Kanit: {evidence} "
                    f"Etki: {impact_line} "
                    f"Ilk aksiyon: {first_action}"
                )
        elif expertise_level == "casual":
            message = f"{analysis_summary} {confidence_phrase}"
        elif response_mode == "brief":
            message = f"{analysis_summary} {early_signal_note}"
        elif response_mode == "warning":
            message = (
                f"{temporal_observation or analysis_summary} "
                f"{business_impact_note or 'Bu sinyal dikkat istiyor.'} "
                f"{predictive_outlook}"
            )
        else:
            message = (
                f"{analysis_summary} "
                f"{early_signal_note} "
                f"{business_impact_note if business_impact_note else ''} "
                f"{(predictive_outlook if explicit_detail else '').strip()}"
            )
        if continuity_notes and explicit_detail:
            message += f"\n\n{continuity_notes[0]}"
        message = " ".join(message.split())

        quick_actions = self._quick_actions(
            intent,
            delivery_hit,
            negative_reviews_up,
            sales_down,
            domain=domain,
            campaign_allowed=campaign_allowed,
        )
        quick_replies = ["Ana nedeni acikla", "Ilk adimi netlestir"]
        if response_mode == "brief" or expertise_level == "casual":
            quick_replies = ["Kanitlari goster", "Ilk adimi soyle"]
        if domain in {"analytics", "support", "general_chat"}:
            quick_actions = [x for x in quick_actions if str(x.get("action") or "") not in {"create_campaign", "generate_banner"}]
            suggested_actions = [x for x in suggested_actions if "kampanya" not in str(x).lower()]
        follow_up_question = "Istersen teslimat kaynakli yorumlari kategori bazinda ayirabilirim."
        if response_mode == "brief" or expertise_level == "casual" or not explicit_detail:
            follow_up_question = ""

        return {
            "type": "analysis",
            "tone": tone,
            "intent": intent,
            "domain": domain,
            "confidence": confidence,
            "message": message,
            "sections": sections,
            "suggested_actions": suggested_actions[:2],
            "quick_replies": quick_replies,
            "quick_actions": quick_actions,
            "related_entities": self._entity_refs(context),
            "follow_up_question": follow_up_question,
            "analysis_summary": analysis_summary,
            "reasoning_response": reasoning_response,
            "alternative_hypothesis": alternative_hypothesis,
            "temporal_observation": temporal_observation,
            "continuity_notes": continuity_notes,
            "risk_trajectory": risk_trajectory,
            "early_signal_note": early_signal_note,
            "predictive_outlook": predictive_outlook,
            "business_impact_note": business_impact_note,
            "business_consequences": business_consequences,
            "domain_insights": domain_insights,
            "campaign_intelligence": campaign_intelligence,
            "segment_awareness": segment_awareness,
            "category_family": str(commerce.get("category_family") or ""),
            "category_label": str(commerce.get("category_label") or ""),
            "expertise_level": expertise_level,
            "expertise_reason": expertise_reason,
            "evidence_signals": evidence_signals,
            "confidence_phrase": confidence_phrase,
            "reasoning_transparency": reasoning_transparency,
            "trust_signal_strength": trust_signal_strength,
            "response_mode": response_mode,
            "response_word_target": response_word_target,
            "recommendation_summary": recommendation_summary,
            "detected_risks": detected_risks,
            "detected_opportunities": detected_opportunities,
            "tool_count": len(tool_states),
        }
