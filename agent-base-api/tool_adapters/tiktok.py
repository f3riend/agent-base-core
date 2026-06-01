"""TikTok channel adapter — stub."""

from __future__ import annotations

from tool_adapters import FeatureDisabledError, SOCIAL_PUBLISH_LIVE


class TikTokAdapter:
    provider_id = "tiktok"
    display_name = "TikTok"

    def publish_post(
        self,
        *,
        user_id: int,
        account_handle: str,
        caption: str,
        image_url: str | None = None,
        hashtags: list[str] | None = None,
    ) -> dict:
        if not SOCIAL_PUBLISH_LIVE:
            raise FeatureDisabledError(
                "SOCIAL_PUBLISH_LIVE=0 — gerçek TikTok publish devre dışı"
            )
        return {
            "ok": False,
            "provider": "tiktok",
            "error": "TikTok OAuth + content publish henüz implement edilmedi",
            "note": "Phase D2'de eklenecek (TikTok Open API + content moderation).",
        }

    def health_check(self, user_id: int) -> dict:
        return {
            "provider": "tiktok",
            "credential_present": False,
            "live_flag": SOCIAL_PUBLISH_LIVE,
            "ready": False,
            "note": "Adapter geliştirme aşamasında",
        }
