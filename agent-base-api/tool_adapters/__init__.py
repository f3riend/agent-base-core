"""Real channel adapters — Phase D'nin temelini atan iskelet.

`SOCIAL_PUBLISH_LIVE=1` ortamında bu paketten adapter çağrıları yapılır.
Default 0 — herhangi bir adapter `raise FeatureDisabledError` ile döner ve
publisher_node fake (draft) davranışına döner.

Tasarım: her adapter Protocol'e uygun bir sınıf. Yeni adapter eklemek:
    1. tool_adapters/<provider>.py yaz
    2. publish_post / health_check methodlarını implement et
    3. get_adapter dispatch'ine ekle

Bu dosyalar SHIPPED real network çağrısı YAPMAZ — sadece schema, signature,
feature flag karşı kontrol, error class'ları, ve `would_have_done` log'u.
Gerçek HTTP çağrıları Phase D'nin ileri turlarında eklenecek.
"""

from __future__ import annotations

import os
from typing import Protocol


SOCIAL_PUBLISH_LIVE = os.environ.get("SOCIAL_PUBLISH_LIVE", "0") == "1"


class FeatureDisabledError(RuntimeError):
    """Adapter çağrıldı ama SOCIAL_PUBLISH_LIVE=0 — fake'e geri dön."""


class AdapterCredentialError(RuntimeError):
    """Adapter çağrıldı ama bu provider için aktif credential yok."""


class ChannelAdapter(Protocol):
    """Tüm adapter'ların uyması gereken minimum interface."""

    provider_id: str
    display_name: str

    def publish_post(
        self,
        *,
        user_id: int,
        account_handle: str,
        caption: str,
        image_url: str | None = None,
        hashtags: list[str] | None = None,
    ) -> dict:
        """Real publish — feature flag kapalıysa FeatureDisabledError."""
        ...

    def health_check(self, user_id: int) -> dict:
        """Provider erişilebilir mi? Credentials valid mi? Quota ok mu?"""
        ...


def get_adapter(provider: str) -> ChannelAdapter | None:
    """Provider id'den adapter instance döndür (yoksa None)."""
    from tool_adapters import facebook, instagram, tiktok
    _instagram = instagram.InstagramAdapter()
    adapters: dict[str, ChannelAdapter] = {
        "instagram":       _instagram,
        "story":           _instagram,   # story → instagram adapter
        "instagram_story": _instagram,   # instagram_story → instagram adapter
        "facebook":        facebook.FacebookAdapter(),
        "tiktok":          tiktok.TikTokAdapter(),
    }
    return adapters.get(provider)