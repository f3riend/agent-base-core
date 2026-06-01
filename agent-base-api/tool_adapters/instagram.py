"""
Instagram channel adapter — gerçek Graph API HTTP.

Tur 3'te gerçek HTTP çağrıları eklendi (requests). Üç katmanlı güvenlik:
    1. `SOCIAL_PUBLISH_LIVE=1` env flag (default 0)
    2. Aktif credential (Fernet-encrypted token blob)
    3. `allow_real=True` parameter (publisher_node geçer; default False)

Token formatı:
    - JSON blob (önerilen): {"access_token": "...", "ig_user_id": "..."}
    - Bare string: access_token; ig_user_id account_handle'dan türetilir

Graph API akışı:
    POST /{ig-user-id}/media        → creation_id
    POST /{ig-user-id}/media_publish → media_id

Hata davranışı: hiçbir HTTP hatası publisher node'u bozmaz; tüm hatalar
yakalanır ve `{"ok": False, "error": ...}` döndürülür. publisher_node
sonra fake (draft) moduna geri döner.
"""

from __future__ import annotations

import json
import os
import re
import requests

from tool_adapters import (
    AdapterCredentialError,
    FeatureDisabledError,
    SOCIAL_PUBLISH_LIVE,
)


GRAPH_BASE = os.environ.get("META_GRAPH_API_BASE", "https://graph.facebook.com/v18.0")
GRAPH_TIMEOUT = float(os.environ.get("META_GRAPH_TIMEOUT_SEC", "20"))


# Test handle pattern — bunlara real HTTP atmıyoruz (safety net)
_TEST_HANDLE_RE = re.compile(
    r"^(test_|demo_|sandbox_|ai_ops_test|smoke)",
    re.IGNORECASE,
)


def _parse_token_blob(raw_token: str) -> tuple[str, str | None]:
    """Token JSON ise (access_token, ig_user_id) döndür; bare ise (token, None)."""
    if not raw_token:
        return "", None
    raw = raw_token.strip()
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
            return (
                str(data.get("access_token") or data.get("token") or "").strip(),
                str(data.get("ig_user_id") or data.get("user_id") or "").strip() or None,
            )
        except json.JSONDecodeError:
            return raw, None
    return raw, None


class InstagramAdapter:
    provider_id = "instagram"
    display_name = "Instagram"

    def publish_post(
        self,
        *,
        user_id: int,
        account_handle: str,
        caption: str,
        image_url: str | None = None,
        hashtags: list[str] | None = None,
        allow_real: bool = True,
    ) -> dict:
        if not SOCIAL_PUBLISH_LIVE:
            raise FeatureDisabledError(
                "SOCIAL_PUBLISH_LIVE=0 — gerçek Instagram publish devre dışı"
            )
        if not allow_real:
            raise FeatureDisabledError(
                "Çağıran allow_real=False vermiş — adapter çağrısı atlanıyor."
            )

        # Test/sandbox handle'larında real HTTP yapma — accidental publish'ı önle
        if _TEST_HANDLE_RE.match(account_handle or ""):
            return {
                "ok": False,
                "provider": "instagram",
                "account_handle": account_handle,
                "skipped_real_call": True,
                "reason": "test_handle_pattern",
                "note": (
                    "Hesap adı test pattern'i — gerçek Graph API çağrısı "
                    "yapılmadı (güvenlik). Real publish için canlı bir "
                    "handle kullan."
                ),
            }

        # Credential resolve
        try:
            from social_credentials import get_credential
            cred = get_credential(user_id, "instagram", account_handle=account_handle)
        except Exception as exc:
            raise AdapterCredentialError(
                f"Instagram credential resolve hata: {exc}"
            )

        access_token, ig_user_id = _parse_token_blob(cred.token or "")
        if not ig_user_id:
            ig_user_id = (cred.account_handle or "").strip()
        if not access_token or not ig_user_id:
            return {
                "ok": False,
                "provider": "instagram",
                "error": "missing_access_token_or_ig_user_id",
                "note": (
                    "Credential token JSON blob içinde access_token ve "
                    "ig_user_id alanları gerekir."
                ),
            }

        full_caption = caption or ""
        if hashtags:
            tag_str = " ".join(f"#{h.lstrip('#')}" for h in hashtags)
            full_caption = f"{full_caption}\n\n{tag_str}".strip()

        # Step 1: media container
        try:
            container_url = f"{GRAPH_BASE}/{ig_user_id}/media"
            container_params = {
                "image_url": image_url or "https://placehold.co/1080x1080/png?text=AI",
                "caption": full_caption,
                "access_token": access_token,
            }
            r1 = requests.post(container_url, data=container_params,
                               timeout=GRAPH_TIMEOUT)
        except requests.RequestException as exc:
            return {"ok": False, "provider": "instagram",
                    "error": f"container_http_failed: {exc}"}

        if r1.status_code >= 400:
            return {
                "ok": False, "provider": "instagram",
                "stage": "container", "status_code": r1.status_code,
                "error": _safe_error_body(r1),
            }
        try:
            container_id = r1.json().get("id")
        except ValueError:
            return {"ok": False, "provider": "instagram",
                    "error": "container_response_not_json"}
        if not container_id:
            return {"ok": False, "provider": "instagram",
                    "error": "no_container_id_returned"}

        # Step 2: media_publish
        try:
            publish_url = f"{GRAPH_BASE}/{ig_user_id}/media_publish"
            publish_params = {
                "creation_id": container_id,
                "access_token": access_token,
            }
            r2 = requests.post(publish_url, data=publish_params,
                               timeout=GRAPH_TIMEOUT)
        except requests.RequestException as exc:
            return {"ok": False, "provider": "instagram",
                    "stage": "publish", "error": f"publish_http_failed: {exc}"}

        if r2.status_code >= 400:
            return {
                "ok": False, "provider": "instagram",
                "stage": "publish", "status_code": r2.status_code,
                "error": _safe_error_body(r2),
            }

        try:
            published = r2.json()
        except ValueError:
            published = {}

        return {
            "ok": True,
            "provider": "instagram",
            "account_handle": cred.account_handle,
            "container_id": container_id,
            "post_id": published.get("id"),
            "raw_response": published,
            "live_call": True,
        }

    def health_check(self, user_id: int) -> dict:
        try:
            from social_credentials import try_get_credential
            cred = try_get_credential(user_id, "instagram")
        except Exception:
            cred = None
        return {
            "provider": "instagram",
            "credential_present": cred is not None,
            "live_flag": SOCIAL_PUBLISH_LIVE,
            "ready": (cred is not None) and SOCIAL_PUBLISH_LIVE,
        }


def _safe_error_body(response) -> str:
    """Response body'yi güvenli şekilde stringe çevir."""
    try:
        return json.dumps(response.json())[:400]
    except ValueError:
        return (response.text or "")[:400]
