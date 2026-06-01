from __future__ import annotations

import json
import re
from typing import Any

from .llm_gateway import LLMGateway
from .operation_semantics import OperationSemantics


class SemanticOperationInterpreter:
    """LLM-first semantic interpreter. Rules stay as fallback/safety."""

    def __init__(self, llm: LLMGateway | None = None, fallback: OperationSemantics | None = None) -> None:
        self.llm = llm or LLMGateway()
        self.fallback = fallback or OperationSemantics()

    def _operation_action_from_type(self, operation_type: str) -> str:
        t = str(operation_type or "").strip().lower()
        mapping = {
            "create_content": "create_content",
            "generate_asset": "create_content",
            "save_draft": "save_draft",
            "schedule_post": "schedule_content",
            "publish_post": "publish_content",
            "create_and_schedule": "create_and_schedule",
            "create_and_publish": "create_and_publish",
        }
        return mapping.get(t, "create_content")

    def _default_operation_flow(self, action: str) -> list[str]:
        if action == "create_and_schedule":
            return ["create_content", "generate_asset", "approval", "schedule_post"]
        if action == "create_and_publish":
            return ["create_content", "generate_asset", "approval", "schedule_post", "publish_post"]
        if action == "publish_content":
            return ["schedule_post", "publish_post"]
        if action == "schedule_content":
            return ["schedule_post"]
        if action == "save_draft":
            return ["create_content", "generate_asset", "save_draft"]
        return ["create_content", "generate_asset", "approval"]

    def _extract_json(self, text: str) -> dict[str, Any]:
        raw = str(text or "").strip()
        if not raw:
            return {}
        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            return {}
        try:
            obj = json.loads(match.group(0))
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}

    def _extract_active_context(self, ctx: dict[str, Any]) -> dict[str, Any]:
        previous_ops = list(ctx.get("previous_operations") or [])
        pending_actions = list(ctx.get("pending_actions") or [])
        active_operation_id = ""
        active_campaign_id = ""
        active_asset_id = ""
        last_pending_approval = ""

        for item in reversed(previous_ops):
            data = dict(item or {})
            kind = str(data.get("kind") or "").strip().lower()
            meta = dict(data.get("meta") or {})
            if not active_operation_id:
                active_operation_id = str(meta.get("operation_id") or data.get("operation_id") or "")
            if not active_campaign_id and kind in {"scheduled_post_created", "campaign_created"}:
                active_campaign_id = str(meta.get("scheduled_post_id") or meta.get("campaign_id") or data.get("campaign_id") or "")
            if not active_asset_id and kind == "asset_generated":
                active_asset_id = str(meta.get("asset_id") or data.get("asset_id") or data.get("image_url") or "")
            if not last_pending_approval and kind == "approval_requested":
                last_pending_approval = str(meta.get("approval_id") or data.get("approval_id") or "")
            if active_operation_id and active_campaign_id and active_asset_id and last_pending_approval:
                break

        if not last_pending_approval:
            for p in reversed(pending_actions):
                status = str((p or {}).get("status") or "").lower()
                if status in {"pending", "waiting_approval"}:
                    last_pending_approval = str((p or {}).get("id") or "")
                    break

        return {
            "active_operation_id": active_operation_id,
            "active_campaign_id": active_campaign_id,
            "active_asset_id": active_asset_id,
            "last_pending_approval": last_pending_approval,
        }

    def _infer_domain_from_message(self, message: str, sem: dict[str, Any] | None = None) -> str:
        txt = str(message or "").lower()
        analytics_kw = (
            "neden", "dustu", "dusus", "satis azaldi", "yorumlari analiz", "risk var mi", "performans nasil",
            "neyi yanlis", "analiz et", "yorum", "trend", "performans",
        )
        content_kw = ("post", "story", "hikaye", "reel", "banner", "kampanya", "gonderi", "icerik")
        schedule_kw = ("takvime", "planla", "zamanla", "schedule", "sonra paylas", "yarin paylas", "ayin")
        publish_kw = ("yayinla", "canliya", "hemen paylas")
        support_kw = ("yorumlara cevap", "soru cevap", "destek", "musteri sorusu", "cevapla")
        strategy_kw = ("strateji", "optimiz", "buyut", "konumlandirma", "fiyat strateji")

        if any(k in txt for k in analytics_kw):
            return "analytics"
        if any(k in txt for k in support_kw):
            return "support"
        if any(k in txt for k in publish_kw):
            return "publishing"
        if any(k in txt for k in schedule_kw):
            return "scheduling"
        if any(k in txt for k in strategy_kw):
            return "strategy"
        if any(k in txt for k in content_kw):
            return "content_ops"

        intent = str((sem or {}).get("intent") or "")
        if intent in {"analyze_reviews", "general_analysis"}:
            return "analytics"
        if intent in {"create_campaign", "generate_banner"}:
            return "content_ops"
        if intent == "approve_campaign":
            action = str((sem or {}).get("operation_action") or "")
            if action == "publish_content":
                return "publishing"
            return "scheduling"
        return "general_chat"

    def _apply_domain_routing(self, sem: dict[str, Any], message: str) -> dict[str, Any]:
        out = dict(sem or {})
        domain = str(out.get("domain") or "").strip().lower()
        if not domain:
            domain = self._infer_domain_from_message(message, out)
        out["domain"] = domain

        txt = str(message or "").lower()
        if any(k in txt for k in ("kisaca anlat", "kisaca ozetle", "kisa anlat", "ozet gec")):
            out["domain"] = "analytics"
            out["intent"] = "general_analysis"
            out["operation_type"] = "analyze"
            out["operation_action"] = "create_content"
            out["operation_flow"] = ["analyze"]
            out["requires_approval"] = False
            return out
        if domain == "analytics":
            if any(k in txt for k in ("yorum", "yorumlari analiz", "yorum analizi")):
                out["intent"] = "analyze_reviews"
            else:
                out["intent"] = "general_analysis"
            out["operation_type"] = "analyze"
            out["operation_action"] = "create_content"
            out["operation_flow"] = ["analyze"]
            out["requires_approval"] = False
        elif domain == "strategy":
            out["intent"] = "optimize_campaign"
            out["operation_type"] = "analyze"
            out["operation_flow"] = ["analyze", "strategy"]
            out["requires_approval"] = False
        elif domain == "support":
            out["intent"] = "general_analysis"
            out["operation_type"] = "analyze"
            out["operation_flow"] = ["analyze", "support_response"]
            out["requires_approval"] = False
        elif domain == "scheduling":
            out["intent"] = "approve_campaign"
            out["operation_type"] = "schedule_post"
            out["operation_action"] = "schedule_content"
            out["operation_flow"] = ["schedule_post"]
        elif domain == "publishing":
            out["intent"] = "approve_campaign"
            out["operation_type"] = "publish_post"
            out["operation_action"] = "publish_content"
            out["operation_flow"] = ["schedule_post", "publish_post"]
        elif domain == "content_ops":
            if str(out.get("intent") or "") not in {"create_campaign", "generate_banner"}:
                out["intent"] = "create_campaign"
        return out

    def _content_ambiguity(self, message: str) -> bool:
        txt = str(message or "").lower()
        has_content_action = any(k in txt for k in ("olustur", "hazirla", "cikar", "yap", "post", "hikaye", "story", "reel", "banner"))
        has_specific_type = any(k in txt for k in ("post", "feed", "hikaye", "story", "reel", "banner"))
        return has_content_action and not has_specific_type

    def _context_defaults(self, ctx: dict[str, Any]) -> dict[str, Any]:
        prev_ops = list(ctx.get("previous_operations") or [])
        msg_hist = list(ctx.get("chat_history") or [])
        inferred_platform = ""
        inferred_content = ""
        inferred_tone = str(ctx.get("brand_tone") or "").strip()
        platform_hits: dict[str, int] = {"instagram": 0, "web": 0, "email": 0}
        content_hits: dict[str, int] = {
            "instagram_feed_post": 0,
            "instagram_story": 0,
            "instagram_reel": 0,
            "social_banner": 0,
            "web_banner": 0,
            "email_campaign": 0,
        }
        for row in prev_ops[-24:]:
            text_blob = json.dumps(row, ensure_ascii=False, default=str).lower()
            if "instagram" in text_blob:
                platform_hits["instagram"] += 1
            if "email" in text_blob:
                platform_hits["email"] += 1
            if "web_banner" in text_blob or "web banner" in text_blob:
                platform_hits["web"] += 1
            if "instagram_story" in text_blob or "story" in text_blob or "hikaye" in text_blob:
                content_hits["instagram_story"] += 1
            if "instagram_reel" in text_blob or "reel" in text_blob:
                content_hits["instagram_reel"] += 1
            if "social_banner" in text_blob or ("banner" in text_blob and "web" not in text_blob):
                content_hits["social_banner"] += 1
            if "instagram_feed_post" in text_blob or "feed" in text_blob or "post" in text_blob:
                content_hits["instagram_feed_post"] += 1
            if "email_campaign" in text_blob:
                content_hits["email_campaign"] += 1
            if "web_banner" in text_blob:
                content_hits["web_banner"] += 1

        if sum(platform_hits.values()) > 0:
            inferred_platform = max(platform_hits.items(), key=lambda x: x[1])[0]
        if sum(content_hits.values()) > 0:
            inferred_content = max(content_hits.items(), key=lambda x: x[1])[0]

        active_platform = str(ctx.get("active_platform") or "").strip().lower()
        if active_platform in {"instagram", "web", "email"}:
            inferred_platform = active_platform

        last_user = ""
        for row in reversed(msg_hist):
            if str(row.get("role") or "") == "user":
                last_user = str(row.get("content") or "").lower()
                break
        if not inferred_tone and any(k in last_user for k in ("premium", "sicak", "samimi", "minimal")):
            inferred_tone = "premium soft" if "premium" in last_user else "warm"

        return {
            "platform": inferred_platform,
            "content_type": inferred_content,
            "tone": inferred_tone,
            "has_history_signal": bool(prev_ops),
            "history_count": len(prev_ops),
        }

    def _needs_clarification(self, sem: dict[str, Any], message: str) -> tuple[bool, str]:
        conf = float(sem.get("confidence") or sem.get("semantic_confidence") or 0.0)
        domain = str(sem.get("domain") or "").strip().lower()
        intent = str(sem.get("intent") or "")
        op_type = str(sem.get("operation_type") or "")
        text = str(message or "").strip()
        if domain in {"analytics", "strategy", "support", "general_chat"}:
            return False, ""
        if domain in {"content_ops", "scheduling", "publishing"}:
            if not self._content_ambiguity(text):
                return False, ""
        if conf >= 0.52:
            return False, ""
        if intent in {"create_campaign", "approve_campaign"} and op_type:
            return False, ""
        if len(text.split()) <= 2:
            return True, "Bunu Instagram feed gonderisi olarak planlamami ister misin?"
        return True, "Bunu Instagram feed mi, hikaye mi yoksa reels olarak mi ilerletelim?"

    def _apply_context_overrides(self, sem: dict[str, Any], defaults: dict[str, Any], message: str) -> dict[str, Any]:
        out = dict(sem or {})
        msg = str(message or "").lower()
        vague = not any(k in msg for k in ("story", "hikaye", "reel", "post", "banner", "feed", "email", "web"))
        fallback_conf = float(out.get("confidence") or out.get("semantic_confidence") or 0.0)

        if str(defaults.get("platform") or "") and (not str(out.get("platform") or "") or (vague and fallback_conf <= 0.72)):
            out["platform"] = str(defaults.get("platform") or out.get("platform") or "instagram")
        if str(defaults.get("content_type") or "") and (not str(out.get("content_type") or "") or (vague and fallback_conf <= 0.72)):
            out["content_type"] = str(defaults.get("content_type") or out.get("content_type") or "instagram_feed_post")
        if str(defaults.get("tone") or "") and (not str(out.get("tone") or "") or str(out.get("tone") or "") == "balanced"):
            out["tone"] = str(defaults.get("tone") or out.get("tone") or "balanced")
        return out

    def _merge_with_fallback(self, base: dict[str, Any], llm_sem: dict[str, Any], source: str) -> dict[str, Any]:
        llm = dict(llm_sem or {})
        operation_type = str(llm.get("operation_type") or "")
        legacy = {
            "intent": llm.get("intent"),
            "platform": llm.get("platform"),
            "content_type": llm.get("content_type"),
            "operation_action": self._operation_action_from_type(operation_type) if operation_type else llm.get("operation_action"),
            "target_date": llm.get("scheduled_at"),
        }
        merged = self.fallback.apply_fallback(base, legacy)
        action = str(merged.get("operation_action") or "create_content")
        merged["operation_type"] = operation_type or str(merged.get("operation_type") or "create_content")
        merged["objective"] = str(llm.get("objective") or merged.get("objective") or "icerik performansini iyilestirme")
        merged["tone"] = str(llm.get("tone") or merged.get("tone") or "balanced")
        merged["target_audience"] = str(llm.get("target_audience") or merged.get("target_audience") or "genel kitle")
        merged["scheduled_at"] = str(llm.get("scheduled_at") or merged.get("scheduled_at") or merged.get("target_date") or "")
        merged["operation_flow"] = list(llm.get("operation_flow") or merged.get("operation_flow") or self._default_operation_flow(action))
        merged["semantic_notes"] = list(llm.get("semantic_notes") or merged.get("semantic_notes") or [])
        merged["requires_approval"] = bool(llm.get("requires_approval")) if "requires_approval" in llm else bool(merged.get("requires_approval"))
        merged["confidence"] = float(llm.get("confidence") or merged.get("confidence") or merged.get("semantic_confidence") or 0.55)
        merged["semantic_source"] = source
        merged["semantic_confidence"] = float(merged.get("confidence") or merged.get("semantic_confidence") or 0.55)
        merged["domain"] = str(llm.get("domain") or merged.get("domain") or "")
        return merged

    async def interpret(self, *, message: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        ctx = dict(context or {})
        base = self.fallback.interpret(message, ctx)
        defaults = self._context_defaults(ctx)
        active_context = self._extract_active_context(ctx)

        system_prompt = (
            "Sen e-ticaret operasyon semantik yorumlayicisisin. "
            "Kullanicinin dogal dildeki istegini operasyon kontratina cevir. "
            "Eksik alanlari baglamdan tamamla. "
            "Sadece gecerli JSON don; aciklama yazma."
        )
        payload = {
            "mesaj": message,
            "urun": ctx.get("product_item") or {},
            "urun_metrikleri": ctx.get("product_overview") or {},
            "marka_tonu": ctx.get("brand_tone") or "",
            "onceki_operasyonlar": list(ctx.get("previous_operations") or [])[-12:],
            "konusma_gecmisi": list(ctx.get("chat_history") or [])[-12:],
            "aktif_calisma_baglami": {
                "active_platform": ctx.get("active_platform") or "",
                "store_id": ctx.get("store_id") or "",
                "product_id": ctx.get("product_id") or "",
                "active_operation_id": active_context.get("active_operation_id") or "",
                "active_campaign_id": active_context.get("active_campaign_id") or "",
                "active_asset_id": active_context.get("active_asset_id") or "",
                "last_pending_approval": active_context.get("last_pending_approval") or "",
            },
            "baglamsal_defaultlar": defaults,
            "mevcut_semantik_fallback": base,
            "yorum_notu": (
                "Tarih ifadelerini dogal yorumla (4 gun sonra, haftaya cuma, yarin aksam, anneler gununden once). "
                "Vague mesaji operasyon niyetine cevir. Detay eksikse makul varsayimlari kullan."
            ),
            "json_kontrat": {
                "domain": "analytics | content_ops | scheduling | publishing | support | strategy | general_chat",
                "intent": "create_campaign | approve_campaign | generate_banner | general_analysis",
                "platform": "instagram | web | email",
                "content_type": (
                    "instagram_feed_post | instagram_story | instagram_reel | "
                    "social_banner | web_banner | email_campaign"
                ),
                "operation_type": (
                    "create_content | save_draft | schedule_post | publish_post | "
                    "create_and_schedule | create_and_publish"
                ),
                "objective": "string",
                "tone": "string",
                "target_audience": "string",
                "scheduled_at": "ISO datetime string",
                "operation_flow": ["string"],
                "requires_approval": "boolean",
                "confidence": "0-1",
                "semantic_notes": ["string"],
            },
        }
        user_prompt = json.dumps(payload, ensure_ascii=False, default=str)

        try:
            raw = await self.llm.generate_with_stream(system_prompt=system_prompt, user_prompt=user_prompt, on_chunk=None)
            llm_sem = self._extract_json(raw)
            if not llm_sem:
                merged = self._merge_with_fallback(base, {}, "rule_fallback")
                merged = self._apply_context_overrides(merged, defaults, message)
                merged = self._apply_domain_routing(merged, message)
                merged.update(active_context)
                needs, question = self._needs_clarification(merged, message)
                merged["requires_clarification"] = needs
                merged["clarification_question"] = question
                return merged
            merged = self._merge_with_fallback(base, llm_sem, "llm_primary")
            merged = self._apply_context_overrides(merged, defaults, message)
            merged = self._apply_domain_routing(merged, message)
            if float(merged.get("confidence") or 0.0) < 0.55:
                merged = self._merge_with_fallback(base, llm_sem, "llm_low_confidence_fallback")
                merged = self._apply_context_overrides(merged, defaults, message)
                merged = self._apply_domain_routing(merged, message)
            merged.update(active_context)
            needs, question = self._needs_clarification(merged, message)
            merged["requires_clarification"] = needs
            merged["clarification_question"] = question
            return merged
        except Exception:
            merged = self._merge_with_fallback(base, {}, "rule_fallback")
            merged = self._apply_context_overrides(merged, defaults, message)
            merged = self._apply_domain_routing(merged, message)
            merged.update(active_context)
            needs, question = self._needs_clarification(merged, message)
            merged["requires_clarification"] = needs
            merged["clarification_question"] = question
            return merged
