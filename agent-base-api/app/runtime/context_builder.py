from __future__ import annotations

import json
from typing import Any


class OperationContextBuilder:
    """Builds structured context payloads for LLM narrative generation."""

    SYSTEM_PROMPT = (
        "Sen operasyonel ve net konusan bir e-ticaret asistanisin. "
        "Teknik log dili kullanma. Veriye dayali, kisa ve akilli yanitlar ver. "
        "Neden-sonuc iliskisi kur, baglamsal hipotezler sun, riskleri ve firsatlari yorumla. "
        "Her zaman dogal Turkce kullan. "
        "Tool adlarini teknik formatta yazma; kullaniciya bulguyu, etkisini ve olasi nedeni anlat. "
        "Rapor okur gibi degil, analist gibi konus. "
        "Gerektiginde 'gibi gorunuyor', 'muhtemelen', 'etkilemis olabilir', 'dikkat cekiyor' gibi ifade kaliplari kullan. "
        "Kesin yargi yerine adim adim dusunen bir akisla yaz. "
        "Tek blok monolog yerine kisa paragraflar kullan. "
        "Mumkunse en az bir alternatif ihtimali de belirt. "
        "Zaman icindeki degisimi yorumla; gerekirse 'son 3 gun', 'gecen haftaya gore', 'yeniden gucleniyor' gibi baglamsal ifadeler kullan. "
        "Mumkun oldugunda riskin yonunu (gucleniyor/zayifliyor/stabil/yeniden) belirt ve 'eger bu trend devam ederse' dilini olculu sekilde kullan. "
        "Abartili kurumsal dil kullanma. Kisa, net ve sahadaki operasyon dilini kullan. "
        "Reasoning'i hafif kanit diliyle seffaflastir: kisa ve dogal sekilde hangi sinyale dayandigini belirt, debug dili kullanma. "
        "Sadece verilen runtime verisine dayan; veride olmayan olay, hafiza, analiz veya gecmis uydurma. "
        "Bosluk varsa varsayim yazma, kisa kal."
    )

    _MODE_PROMPTS = {
        "analiz": "Analiz modu: sayisal sinyalleri ve neden-sonuc iliskisini one cikar.",
        "operasyon": "Operasyon modu: hemen uygulanabilir adimlari ve oncelik sirasini one cikar.",
        "strateji": "Strateji modu: orta vadeli buyume, kampanya ve segment etkisini one cikar.",
        "icerik": "Icerik modu: daha yaratıcı ama veriye dayali metin ve ileti oneri dili kullan.",
    }

    def build_user_prompt(
        self,
        *,
        message: str,
        intent: str,
        context: dict[str, Any],
        tool_states: list[dict[str, Any]],
        pending_actions: list[dict[str, Any]],
        detected_risks: list[str],
        detected_opportunities: list[str],
        suggested_actions: list[str],
        previous_assistant_summary: str,
        conversation_memory_summary: str,
        mode: str,
        response_mode: str = "detailed",
        response_word_target: int = 130,
        expertise_level: str = "operational",
    ) -> str:
        mode_key = str(mode or "analiz").strip().lower()
        mode_prompt = self._MODE_PROMPTS.get(mode_key, self._MODE_PROMPTS["analiz"])
        payload = {
            "kullanici_mesaji": message,
            "intent": intent,
            "mod": mode_key,
            "urun": context.get("product_item") or {},
            "metrikler": context.get("product_overview") or {},
            "yorumlar": context.get("product_reviews") or [],
            "sss": context.get("product_faq") or [],
            "destek_kayitlari": context.get("product_support_tickets") or [],
            "gunluk_metrikler": context.get("product_metrics_daily") or [],
            "urun_varliklari": context.get("product_assets") or [],
            "ai_icgoruleri": context.get("product_insights") or [],
            "onceki_operasyonlar": context.get("product_history") or context.get("previous_operations") or [],
            "zaman_izleri": context.get("temporal_signals") or [],
            "ongoru_sinyalleri": context.get("predictive_signals") or [],
            "ticari_icgoruler": context.get("domain_insights") or [],
            "kampanya_dinamigi": context.get("campaign_intelligence") or [],
            "segment_notlari": context.get("segment_awareness") or [],
            "kanit_sinyalleri": context.get("evidence_signals") or [],
            "guven_ifadesi": context.get("confidence_phrase") or "",
            "guven_sinyal_gucu": context.get("trust_signal_strength") or "",
            "dusunce_seffafligi": context.get("reasoning_transparency") or "",
            "kategori_baglami": {
                "aile": context.get("category_family") or "",
                "etiket": context.get("category_label") or "",
            },
            "onceki_sohbet": context.get("chat_history") or [],
            "konusma_ozeti": conversation_memory_summary,
            "bekleyen_aksiyonlar": pending_actions,
            "tool_durumlari": tool_states,
            "tespit_edilen_riskler": detected_risks,
            "tespit_edilen_firsatlar": detected_opportunities,
            "onerilen_aksiyonlar": suggested_actions,
            "fallback_ozet": previous_assistant_summary,
            "yanit_disiplini": {"mod": response_mode, "hedef_kelime": response_word_target, "uzmanlik": expertise_level},
            "operasyon_semantigi": context.get("operation_semantics") or {},
            "yasam_dongusu": context.get("lifecycle_state") or "",
            "aktif_operasyon_baglami": {
                "active_operation_id": context.get("active_operation_id") or "",
                "active_campaign_id": context.get("active_campaign_id") or "",
                "active_asset_id": context.get("active_asset_id") or "",
                "last_pending_approval": context.get("last_pending_approval") or "",
            },
        }
        mode_instruction = {
            "brief": "Yanit cok kisa olsun; en fazla 3 cumle yaz.",
            "warning": "Yanit net ve odakli olsun; 55-100 kelime araliginda kal, kritik etkiyi one cikar.",
            "detailed": "Yanit analitik ama olculu olsun; 4-6 cumleyi gecme.",
        }.get(str(response_mode or "detailed").lower(), "Yanit uzunlugunu olculu tut.")
        expertise_instruction = {
            "casual": "Sade, net, insansi dil kullan. Danisman jargonu kullanma.",
            "operational": "Operasyon odakli ama sade bir denge kur.",
            "expert": "Uzman yorumu kullan ama gereksiz agir dilden kac.",
            "strategic": "Stratejik etki, senaryo ve orta vade sonucunu kisaca bagla.",
        }.get(str(expertise_level or "operational").lower(), "Duruma uygun uzmanlik tonu kullan.")
        strict_analytics = mode_key == "analiz"
        structure_instruction = (
            "Yanit formati yalnizca su 4 adimdan olussun: Problem, Kanit, Etki, Ilk aksiyon. "
            if strict_analytics
            else "Yanit tek akista, dogal ve kisa paragraflarla ilerlesin. "
        )
        instruction = (
            "Yukardaki operasyon verilerini kullanarak kullaniciya tek bir dogal yanit ver. "
            f"{structure_instruction}"
            f"{mode_instruction} "
            f"{expertise_instruction} "
            "Varsayim veya genel MBA kalibi kullanma; her cumleyi payload icindeki somut veriye bagla. "
            "Teknik eylem ozeti vermek yerine bulgunun anlamini anlat. "
            "Mekanik kaliplardan, ozellikle 'tamamlandi/olusturuldu/islendi' tekrarindan kac. "
            "Yorumlayici ama sade bir ton kullan. "
            "Cevabi tek blok rapor gibi yazma, kisa ve net tut. "
            "Paragraflar birbirine baglansin; dusunce adim adim netlessin. "
            "Sadece veri destekliyorsa onceki analizlerle bag kur. "
            "Riskin yonunu mutlaka yorumla (gucleniyor, zayifliyor, stabil kaliyor veya yeniden ortaya cikiyor). "
            "Kisaca bir ongoru cikar: eger bu durum devam ederse ne olabilir, ama yalnizca mevcut veride dayanak varsa belirt. "
            "Confidence dusukse daha yumusak olasilik dili kullan; confidence yuksekse daha net operasyonel uyari dili kullan. "
            "Genel is sunumu dili kullanma; dogrudan bulgu ve sonuc odakli kal. "
            "Kisa bir 'neden boyle dusunuyorum' seffafligi ver ama teknik log diline kacma. "
            "Kullanici acikca kampanya/icerik istemedikce kampanya, banner veya sosyal icerik akisi onermesi yapma. "
            "Runtime verisinde yoksa teslimat, lojistik veya stok problemi uydurma; bu durumda veri olmadigini acikca belirt. "
            "Kullanici istemedikce uzun anlatma. Cevabin sonunda takip sorusu zorunlu degil."
        )
        return f"{mode_prompt}\n{instruction}\n\nVeri:\n{json.dumps(payload, ensure_ascii=False, default=str)}"
