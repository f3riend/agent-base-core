from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any


class OperationSemantics:
    """Semantic operation intent parser with fuzzy understanding."""

    _MONTH_MAP = {
        "ocak": 1,
        "subat": 2,
        "mart": 3,
        "nisan": 4,
        "mayis": 5,
        "haziran": 6,
        "temmuz": 7,
        "agustos": 8,
        "eylul": 9,
        "ekim": 10,
        "kasim": 11,
        "aralik": 12,
    }
    _WEEKDAY_MAP = {
        "pazartesi": 0,
        "sali": 1,
        "carsamba": 2,
        "persembe": 3,
        "cuma": 4,
        "cumartesi": 5,
        "pazar": 6,
    }
    _TYPO_FIXES = {
        "annler": "anneler",
        "instgram": "instagram",
        "instaa": "insta",
        "hikayee": "hikaye",
        "takvme": "takvime",
        "yaynla": "yayinla",
        "paylasalim": "paylasalim",
    }
    _ALIASES: dict[str, tuple[str, ...]] = {
        "instagram": ("instagram", "insta", "ig", "instaya"),
        "story": ("story", "hikaye", "durum"),
        "reel": ("reel", "reels", "video"),
        "post": ("post", "gonderi", "paylasim"),
        "banner": ("banner", "afis"),
        "schedule": ("takvime", "plana", "zamanla", "schedule", "sonra", "koy"),
        "draft": ("taslak", "draft"),
        "publish": ("yayinla", "paylas", "canliya"),
        "create": ("hazirla", "olustur", "yap", "dene", "cikar", "cikalim"),
    }

    def normalize_user_text(self, message: str) -> str:
        txt = str(message or "").strip().lower()
        txt = txt.translate(str.maketrans({"ç": "c", "ğ": "g", "ı": "i", "ö": "o", "ş": "s", "ü": "u"}))
        txt = re.sub(r"[^\w\s:/.+-]", " ", txt)
        txt = re.sub(r"\s+", " ", txt).strip()
        for wrong, right in self._TYPO_FIXES.items():
            txt = re.sub(rf"\b{re.escape(wrong)}\b", right, txt)
        return re.sub(r"\s+", " ", txt).strip()

    def _similar(self, a: str, b: str) -> float:
        return SequenceMatcher(None, a, b).ratio()

    def _contains_semantic(self, normalized_text: str, phrase: str, threshold: float = 0.84) -> bool:
        src = str(normalized_text or "").strip()
        tgt = str(phrase or "").strip()
        if not src or not tgt:
            return False
        if tgt in src:
            return True
        src_tokens = src.split()
        tgt_tokens = tgt.split()
        if len(tgt_tokens) == 1:
            return any(self._similar(tok, tgt_tokens[0]) >= threshold for tok in src_tokens)
        for i in range(0, max(1, len(src_tokens) - len(tgt_tokens) + 1)):
            window = " ".join(src_tokens[i : i + len(tgt_tokens)])
            if self._similar(window, tgt) >= (threshold - 0.06):
                return True
        return False

    def _has_alias(self, normalized_text: str, key: str) -> bool:
        aliases = self._ALIASES.get(key) or ()
        return any(self._contains_semantic(normalized_text, alias) for alias in aliases)

    def _date_from_text(self, normalized_text: str) -> str:
        txt = str(normalized_text or "").lower()
        now = datetime.now(timezone.utc)
        hour = 12
        if "sabah" in txt:
            hour = 9
        elif "aksam" in txt:
            hour = 19

        if "anneler gununden once" in txt:
            year = now.year
            may_first = datetime(year, 5, 1, hour, 0, tzinfo=timezone.utc)
            first_sunday_offset = (6 - may_first.weekday()) % 7
            second_sunday_day = 1 + first_sunday_offset + 7
            mothers_day = datetime(year, 5, second_sunday_day, hour, 0, tzinfo=timezone.utc)
            target = mothers_day - timedelta(days=1)
            if target < now:
                year += 1
                may_first = datetime(year, 5, 1, hour, 0, tzinfo=timezone.utc)
                first_sunday_offset = (6 - may_first.weekday()) % 7
                second_sunday_day = 1 + first_sunday_offset + 7
                mothers_day = datetime(year, 5, second_sunday_day, hour, 0, tzinfo=timezone.utc)
                target = mothers_day - timedelta(days=1)
            return target.isoformat()

        m_days = re.search(r"\b(\d{1,2})\s*gun\s*sonra\b", txt)
        if m_days:
            days = max(1, min(30, int(m_days.group(1))))
            return (now + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()

        if "yarin" in txt:
            return (now + timedelta(days=1)).replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()
        if "bugun" in txt:
            return now.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()

        if "haftaya" in txt:
            for name, weekday in self._WEEKDAY_MAP.items():
                if self._contains_semantic(txt, name):
                    ahead = (weekday - now.weekday()) % 7
                    ahead = 7 if ahead == 0 else ahead + 7
                    return (now + timedelta(days=ahead)).replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()

        for name, weekday in self._WEEKDAY_MAP.items():
            if self._contains_semantic(txt, name):
                ahead = (weekday - now.weekday()) % 7
                ahead = 7 if ahead == 0 else ahead
                return (now + timedelta(days=ahead)).replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()

        m = re.search(r"\b(?:ayin\s*)?(\d{1,2})(?:si|i|u|une|una|e|de)?\b", txt)
        if m:
            day = int(m.group(1))
            if 1 <= day <= 31:
                try:
                    dt = datetime(now.year, now.month, day, hour, 0, tzinfo=timezone.utc)
                    if dt < now:
                        mm = now.month + 1
                        yy = now.year + (1 if mm > 12 else 0)
                        mm = 1 if mm > 12 else mm
                        dt = datetime(yy, mm, day, hour, 0, tzinfo=timezone.utc)
                    return dt.isoformat()
                except Exception:
                    pass

        match = re.search(r"\b(\d{1,2})\s*(ocak|subat|mart|nisan|mayis|haziran|temmuz|agustos|eylul|ekim|kasim|aralik)\b", txt)
        if match:
            day = int(match.group(1))
            month = self._MONTH_MAP.get(str(match.group(2) or "").strip(), now.month)
            year = now.year
            try:
                dt = datetime(year, month, day, hour, 0, tzinfo=timezone.utc)
                if dt < now:
                    dt = datetime(year + 1, month, day, hour, 0, tzinfo=timezone.utc)
                return dt.isoformat()
            except Exception:
                pass
        return now.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()

    def _finalize(self, sem: dict[str, Any]) -> dict[str, Any]:
        intent = str(sem.get("intent") or "general_analysis")
        action = str(sem.get("operation_action") or "create_content")
        sem["requires_schedule"] = action in {"schedule_content", "publish_content", "create_and_schedule", "create_and_publish"}
        sem["requires_approval"] = intent == "create_campaign" and action != "save_draft"
        sem["publish_now"] = action in {"publish_content", "create_and_publish"}
        op_type_map = {
            "create_content": "create_content",
            "save_draft": "save_draft",
            "schedule_content": "schedule_post",
            "publish_content": "publish_post",
            "create_and_schedule": "create_and_schedule",
            "create_and_publish": "create_and_publish",
        }
        sem["operation_type"] = str(sem.get("operation_type") or op_type_map.get(action, "analyze"))
        sem["scheduled_at"] = str(sem.get("scheduled_at") or sem.get("target_date") or "")
        sem["confidence"] = float(sem.get("confidence") or sem.get("semantic_confidence") or 0.55)
        sem["objective"] = str(sem.get("objective") or "icerik performansini iyilestirme")
        sem["tone"] = str(sem.get("tone") or "balanced")
        sem["target_audience"] = str(sem.get("target_audience") or "genel kitle")
        if not str(sem.get("domain") or "").strip():
            if intent in {"analyze_reviews", "general_analysis"}:
                sem["domain"] = "analytics"
            elif intent in {"create_campaign", "generate_banner"}:
                sem["domain"] = "content_ops"
            elif intent == "approve_campaign":
                sem["domain"] = "publishing" if action == "publish_content" else "scheduling"
            elif intent == "optimize_campaign":
                sem["domain"] = "strategy"
            else:
                sem["domain"] = "general_chat"
        if not isinstance(sem.get("semantic_notes"), list):
            sem["semantic_notes"] = []
        if not isinstance(sem.get("operation_flow"), list):
            sem["operation_flow"] = []
        return sem

    def apply_fallback(self, semantics: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
        merged = dict(semantics or {})
        for key in ("intent", "platform", "content_type", "operation_action", "target_date"):
            val = str((fallback or {}).get(key) or "").strip()
            if val:
                merged[key] = val
        content_type = str(merged.get("content_type") or "")
        if content_type in {"instagram_story", "instagram_reel"}:
            merged["post_format"] = "vertical_9_16"
        elif content_type in {"social_banner", "web_banner"}:
            merged["post_format"] = "landscape_banner"
        elif content_type == "email_campaign":
            merged["post_format"] = "email_layout"
        else:
            merged["post_format"] = "square_1080"
        merged["semantic_confidence"] = max(float(merged.get("semantic_confidence") or 0.0), 0.66)
        merged["semantic_source"] = "llm_fallback"
        return self._finalize(merged)

    def interpret(self, message: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized = self.normalize_user_text(message)
        _ = context

        platform = "instagram"
        content_type = "instagram_feed_post"
        post_format = "square_1080"
        operation_action = "create_content"
        intent = "general_analysis"
        confidence = 0.48

        has_create = self._has_alias(normalized, "create")
        has_schedule = self._has_alias(normalized, "schedule") or self._contains_semantic(normalized, "takvime ekle")
        has_draft = self._has_alias(normalized, "draft")
        has_publish = self._has_alias(normalized, "publish")
        delayed_publish = self._contains_semantic(normalized, "sonra") and self._contains_semantic(normalized, "paylas")
        future_schedule_hint = any(
            token in normalized
            for token in ("yarin", "haftaya", "ayin", "aksam", "sabah", "cuma", "cumartesi", "pazar", "pazartesi", "sali")
        )
        has_approve = self._contains_semantic(normalized, "onayla") or self._contains_semantic(normalized, "approve")

        if self._contains_semantic(normalized, "email campaign") or self._contains_semantic(normalized, "e posta") or self._contains_semantic(normalized, "mail kampanya"):
            platform = "email"
            content_type = "email_campaign"
            post_format = "email_layout"
            intent = "create_campaign"
            confidence = max(confidence, 0.84)
        elif self._contains_semantic(normalized, "web") and self._has_alias(normalized, "banner"):
            platform = "web"
            content_type = "web_banner"
            post_format = "landscape_banner"
            intent = "generate_banner"
            confidence = max(confidence, 0.86)
        elif self._has_alias(normalized, "banner"):
            platform = "instagram"
            content_type = "social_banner"
            post_format = "landscape_banner"
            intent = "generate_banner"
            confidence = max(confidence, 0.84)
        elif self._has_alias(normalized, "story"):
            platform = "instagram"
            content_type = "instagram_story"
            post_format = "vertical_9_16"
            intent = "create_campaign"
            confidence = max(confidence, 0.82)
        elif self._has_alias(normalized, "reel"):
            platform = "instagram"
            content_type = "instagram_reel"
            post_format = "vertical_9_16"
            intent = "create_campaign"
            confidence = max(confidence, 0.82)
        elif self._contains_semantic(normalized, "instagram post") or self._contains_semantic(normalized, "feed") or self._contains_semantic(normalized, "social post") or self._has_alias(normalized, "post"):
            platform = "instagram"
            content_type = "instagram_feed_post"
            post_format = "square_1080"
            intent = "create_campaign"
            confidence = max(confidence, 0.78)

        if has_approve or has_schedule:
            intent = "approve_campaign"
            operation_action = "schedule_content"
            confidence = max(confidence, 0.76)
        if has_publish and not delayed_publish and not future_schedule_hint:
            intent = "approve_campaign"
            operation_action = "publish_content"
            confidence = max(confidence, 0.8)
        elif delayed_publish or (has_publish and future_schedule_hint):
            intent = "approve_campaign"
            operation_action = "schedule_content"
            confidence = max(confidence, 0.79)
        if has_draft:
            intent = "create_campaign"
            operation_action = "save_draft"
            confidence = max(confidence, 0.79)

        if has_create and has_schedule and not has_publish:
            intent = "create_campaign"
            operation_action = "create_and_schedule"
            confidence = max(confidence, 0.84)
        if has_create and has_publish:
            intent = "create_campaign"
            operation_action = "create_and_publish"
            confidence = max(confidence, 0.85)

        if intent == "general_analysis" and (self._contains_semantic(normalized, "kampanya") or has_create):
            intent = "create_campaign"
            confidence = max(confidence, 0.64)

        target_date = self._date_from_text(normalized)
        cta_style = "engagement_cta"
        if content_type == "instagram_story":
            cta_style = "swipe_cta"
        elif content_type == "email_campaign":
            cta_style = "click_through_cta"

        sem = {
            "intent": intent,
            "platform": platform,
            "content_type": content_type,
            "post_format": post_format,
            "operation_action": operation_action,
            "target_date": target_date,
            "cta_style": cta_style,
            "normalized_text": normalized,
            "semantic_confidence": round(min(0.96, max(0.42, confidence)), 2),
            "semantic_source": "rule_fuzzy",
        }
        return self._finalize(sem)

