"""
Facebook channel adapter — gerçek Graph API HTTP.

Instagram adapter'a paralel yapı: SOCIAL_PUBLISH_LIVE + credential + safe handle.
Facebook için tek-adım: POST /{page-id}/feed
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
from tool_adapters.instagram import _parse_token_blob, _safe_error_body


GRAPH_BASE = os.environ.get("META_GRAPH_API_BASE", "https://graph.facebook.com/v18.0")
GRAPH_TIMEOUT = float(os.environ.get("META_GRAPH_TIMEOUT_SEC", "20"))


_TEST_HANDLE_RE = re.compile(
    r"^(test_|demo_|sandbox_|ai_ops_test|smoke)",
    re.IGNORECASE,
)


class FacebookAdapter:
    provider_id = "facebook"
    display_name = "Facebook"

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
                "SOCIAL_PUBLISH_LIVE=0 — gerçek Facebook publish devre dışı"
            )
        if not allow_real:
            raise FeatureDisabledError("allow_real=False — atlandı")

        if _TEST_HANDLE_RE.match(account_handle or ""):
            return {
                "ok": False, "provider": "facebook",
                "skipped_real_call": True, "reason": "test_handle_pattern",
            }

        try:
            from social_credentials import get_credential
            cred = get_credential(user_id, "facebook", account_handle=account_handle)
        except Exception as exc:
            raise AdapterCredentialError(f"Facebook credential hatası: {exc}")

        access_token, page_id = _parse_token_blob(cred.token or "")
        if not page_id:
            page_id = (cred.account_handle or "").strip()
        if not access_token or not page_id:
            return {
                "ok": False, "provider": "facebook",
                "error": "missing_access_token_or_page_id",
            }

        message = caption or ""
        if hashtags:
            tag_str = " ".join(f"#{h.lstrip('#')}" for h in hashtags)
            message = f"{message}\n\n{tag_str}".strip()

        # Facebook page feed — tek-adım publish
        endpoint = (
            f"{GRAPH_BASE}/{page_id}/photos"
            if image_url
            else f"{GRAPH_BASE}/{page_id}/feed"
        )
        params = {
            "message": message,
            "access_token": access_token,
        }
        if image_url:
            params["url"] = image_url

        try:
            r = requests.post(endpoint, data=params, timeout=GRAPH_TIMEOUT)
        except requests.RequestException as exc:
            return {"ok": False, "provider": "facebook",
                    "error": f"http_failed: {exc}"}

        if r.status_code >= 400:
            return {
                "ok": False, "provider": "facebook",
                "status_code": r.status_code,
                "error": _safe_error_body(r),
            }

        try:
            body = r.json()
        except ValueError:
            body = {}

        return {
            "ok": True, "provider": "facebook",
            "account_handle": cred.account_handle,
            "post_id": body.get("id") or body.get("post_id"),
            "raw_response": body,
            "live_call": True,
        }

    def health_check(self, user_id: int) -> dict:
        try:
            from social_credentials import try_get_credential
            cred = try_get_credential(user_id, "facebook")
        except Exception:
            cred = None
        return {
            "provider": "facebook",
            "credential_present": cred is not None,
            "live_flag": SOCIAL_PUBLISH_LIVE,
            "ready": (cred is not None) and SOCIAL_PUBLISH_LIVE,
        }
