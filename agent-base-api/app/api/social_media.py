import json
import os
import re
import time
import uuid
import asyncio
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.core.database import get_db
from app.models.social_document import SocialDocument
from app.models.user import User
from app.schemas.agent import AgentCreateRequest, AgentRunRequest, AgentUpdateRequest, ManagerRunRequest
from app.schemas.content import (
    ActionCommand,
    AIOperateCard,
    AIOperateEvent,
    AIOperateMessage,
    AIOperatePendingAction,
    AIOperateRequest,
    AIOperateResponse,
    AIOperateToolState,
    AnalyzeRequest,
    AutomationChatTriggerRequest,
    AutomationChatTriggerResponse,
    AutomationEventRequest,
    AutomationEventResponse,
    CaptionRequest,
    FlowSessionFeedbackRequest,
    FlowSessionStartRequest,
    HolidayGenerateRequest,
    HolidayGenerateResponse,
    VideoGenerateRequest,
    VideoGenerateResponse,
    ImageGenerateRequest,
    ImageReferenceGenerateRequest,
    ImageReviseRequest,
    InstagramLinkedAccountsRequest,
    PostRequest,
    RevizeRequest,
    TaskStatusResponse,
)
from app.agents.social_media_agent import SocialMediaImageFlow
from app.services.agent_manager_service import AgentManagerService
from app.services.agent_runtime_service import AgentRuntimeService
from app.services.content_service import (
    delete_image_from_storage,
    generate_caption,
    generate_holiday_content,
    generate_social_video,
    generate_images,
    generate_images_from_reference,
    post_multi_photo_to_facebook,
    post_carousel_to_instagram,
    post_story_batch_to_instagram,
    post_photo_to_facebook,
    post_story_to_instagram,
    post_to_instagram,
    preflight_publish_image_urls_for_graph,
    refine_caption,
    revise_image_with_feedback,
    upload_image_bytes_to_storage,
)
from app.services.content_intelligence_service import ContentIntelligenceService
from app.services.task_dispatcher import dispatch, use_celery
from app.integrations.instagram_client import (
    collect_image_urls_for_publish_preflight,
    is_meta_application_request_limit_error,
    list_graph_publish_destinations,
    list_instagram_accounts_for_user_token,
    ordered_publish_urls_for_stories,
    partition_publish_media_urls,
    resolve_instagram_user_id_from_access_token,
    resolve_single_facebook_page_id_if_obvious,
)

_cis = ContentIntelligenceService()

router = APIRouter(prefix="/social-media", tags=["SocialMedia"])
legacy_router = APIRouter(tags=["LegacyApiShim"])
social_media_logger = logger.bind(module="social-media")

manager_service = AgentManagerService()
runtime_service = AgentRuntimeService(manager_service=manager_service)
social_flow = SocialMediaImageFlow()


AUTOMATION_RULES_COLLECTION = "automation_rules"
AUTOMATION_EVENTS_COLLECTION = "automation_events"
AUTOMATION_WORKFLOWS_COLLECTION = "automation_workflows"
STORES_RUNTIME_COLLECTION = "stores_runtime"
SCHEDULED_POSTS_COLLECTION = "scheduled_posts"
COMPOSER_DRAFTS_COLLECTION = "composer_drafts"
APP_SETTINGS_COLLECTION = "app_settings"
APP_SETTINGS_DOC_ID = "api_keys"
CAMPAIGN_CATALOG_SERVER_CACHE_TTL_SEC = 120
OUTBOUND_IP_CACHE_TTL_SEC = 30
DEFAULT_CAMPAIGN_API_BASE_URL = "https://mtlive.sepetler.com/api/ai/v1"
DEFAULT_OUTBOUND_IP_DETECT_URL = "https://api.ipify.org?format=text"
_campaign_catalog_cache: dict[str, dict] = {}
# origin -> en son kaydedilen çıkış IP'si. ipify ile saptanan IP farklılaştığında yenilenir.
_campaign_ip_registration_cache: dict[str, str] = {}
_outbound_ip_cache: tuple[str, float] | None = None


def _campaign_resolve_base_url(raw: str) -> str:
    base = str(raw or "").strip().rstrip("/")
    if base:
        return base
    env_base = (os.getenv("CAMPAIGN_API_BASE_URL") or "").strip().rstrip("/")
    if env_base:
        return env_base
    return DEFAULT_CAMPAIGN_API_BASE_URL

STORE_TEMPLATE_LIBRARY: dict[str, dict[str, Any]] = {
    "welcome_minimal": {
        "id": "welcome_minimal",
        "name": "Welcome Minimal",
        "layout": "single_product_center",
        "text_slots": ["headline", "subline", "cta"],
        "cta_style": "short_invite",
        "visual_direction": "clean background, centered product, soft gradient",
        "color_approach": "light neutral + brand accent",
    },
    "welcome_bold": {
        "id": "welcome_bold",
        "name": "Welcome Bold",
        "layout": "split_left_text_right_visual",
        "text_slots": ["headline", "benefit", "cta"],
        "cta_style": "direct_action",
        "visual_direction": "high contrast hero composition, strong title zone",
        "color_approach": "brand contrast + warm highlight",
    },
    "welcome_story": {
        "id": "welcome_story",
        "name": "Welcome Story",
        "layout": "vertical_story",
        "text_slots": ["hook", "offer", "cta"],
        "cta_style": "tap_to_explore",
        "visual_direction": "vertical safe-zones, quick-read text hierarchy",
        "color_approach": "soft pastel + readable overlay",
    },
}

def _classify_error(exc: Exception) -> str:
    msg = str(exc).lower()
    if "http " in msg or "oauth" in msg or "instagram" in msg or "facebook" in msg:
        return "upstream_service_or_external_api"
    if isinstance(exc, ValueError):
        return "request_validation_or_input"
    if "key" in msg or "token" in msg:
        return "credentials_or_configuration"
    return "application_code_or_unknown"


def _log_api_error(endpoint: str, exc: Exception, payload: dict | None = None) -> None:
    social_media_logger.exception(
        "API_ERROR endpoint={} category={} error_type={} payload={} message={}",
        endpoint,
        _classify_error(exc),
        type(exc).__name__,
        payload or {},
        str(exc),
    )


def _log_graph_publish_exc(endpoint: str, exc: Exception, payload: dict | None = None) -> None:
    """Meta rate/app limits — warning only (full traceback spam yapmasın)."""
    msg_l = str(exc).lower()
    if "application request limit" in msg_l or "2207051" in msg_l:
        social_media_logger.warning(
            "GRAPH_APP_LIMIT endpoint={} payload={} message={}",
            endpoint,
            payload or {},
            str(exc),
        )
        return
    _log_api_error(endpoint, exc, payload)


def _sync_generate_images_task(
    prompt: str,
    count: int,
    platform: str,
    reference_image_url: str | None,
    fal_api_key: str | None,
    openai_api_key: str | None,
    use_gpt: bool = False,
    reference_image_urls: list[str] | None = None,
    output_size: str | None = None,
    skip_professionalization: bool = False,
) -> list[dict[str, str]]:
    """Same branching as Celery ``generate_images_task`` — used when USE_CELERY=false."""
    ref = (reference_image_url or "").strip()
    plat = platform if platform in ("feed", "story", "video") else "feed"
    if ref:
        return generate_images_from_reference(
            reference_image_url=ref,
            prompt=prompt,
            count=count,
            fal_api_key=fal_api_key,
            openai_api_key=openai_api_key,
            platform=plat,
            reference_image_urls=reference_image_urls,
            output_size=output_size,
            skip_professionalization=skip_professionalization,
        )
    return generate_images(
        prompt,
        count,
        fal_api_key=fal_api_key,
        platform=plat,
        openai_api_key=openai_api_key,
        use_gpt=use_gpt,
        output_size=output_size,
    )


def _sync_revise_image_task(
    image_url: str,
    feedback: str,
    count: int,
    platform: str,
    fal_api_key: str | None,
    openai_api_key: str | None,
    reference_image_urls: list[str] | None = None,
    output_size: str | None = None,
    revision_context: str = "social",
) -> list[dict[str, str]]:
    """Same as Celery ``revise_image_task`` body — used when USE_CELERY=false."""
    plat = platform if platform in ("feed", "story", "video") else "feed"
    rc = (revision_context or "social").strip().lower()
    if rc not in ("social", "campaign_banner"):
        rc = "social"
    return revise_image_with_feedback(
        image_url=image_url,
        feedback=feedback,
        count=count,
        fal_api_key=fal_api_key,
        platform=plat,
        openai_api_key=openai_api_key,
        reference_image_urls=reference_image_urls,
        output_size=output_size,
        revision_context=rc,  # type: ignore[arg-type]
    )


def _sync_holiday_generate_task(
    holiday_name: str,
    date_key: str,
    locale: str,
    openai_api_key: str | None,
    fal_api_key: str | None,
    generate_image: bool,
    generate_video: bool,
    extra_instructions: str | None = None,
) -> dict:
    return generate_holiday_content(
        holiday_name=holiday_name,
        date_key=date_key,
        locale=locale,
        openai_api_key=openai_api_key,
        fal_api_key=fal_api_key,
        generate_image=generate_image,
        generate_video=generate_video,
        extra_instructions=extra_instructions,
    )


def _sync_video_generate_task(
    prompt: str,
    fal_api_key: str | None,
    image_url: str | None = None,
    duration_sec: int = 5,
    generate_audio: bool = True,
) -> dict:
    url = generate_social_video(
        prompt,
        fal_api_key=fal_api_key,
        image_url=image_url,
        duration_sec=duration_sec,
        generate_audio=generate_audio,
    )
    return {"video_url": url}


def _sync_caption_generate_task(konu: str, tone: str, openai_api_key: str | None) -> dict:
    caption = generate_caption(konu, tone, openai_api_key=openai_api_key)
    return {"session_id": str(uuid.uuid4())[:10], "caption": caption, "konu": konu}


def _sync_caption_revize_task(mevcut_caption: str, revize_talebi: str, openai_api_key: str | None) -> dict:
    return {"caption": refine_caption(mevcut_caption, revize_talebi, openai_api_key=openai_api_key)}


def _sync_generate_from_reference_task(
    reference_image_url: str,
    prompt: str,
    count: int,
    fal_api_key: str | None,
    openai_api_key: str | None,
    mode: str,
    reference_image_urls: list[str] | None,
    skip_professionalization: bool,
    output_size: str | None = None,
) -> dict:
    images = generate_images_from_reference(
        reference_image_url=reference_image_url,
        prompt=prompt,
        count=count,
        fal_api_key=fal_api_key,
        openai_api_key=openai_api_key,
        mode=mode,
        reference_image_urls=reference_image_urls,
        skip_professionalization=skip_professionalization,
        output_size=output_size,
    )
    return {"images": images, "session_id": str(uuid.uuid4())[:10]}


def _resolve_workspace_openai_key(db: Session, workspace_uid: str, value: str | None = None) -> str:
    """Resolve OpenAI key from request override or workspace app_settings."""
    direct = (value or "").strip()
    if direct:
        return direct

    api_keys_doc = db.scalar(
        select(SocialDocument).where(
            SocialDocument.workspace_uid == workspace_uid,
            SocialDocument.collection == APP_SETTINGS_COLLECTION,
            SocialDocument.doc_id == APP_SETTINGS_DOC_ID,
        )
    )
    if api_keys_doc is not None:
        payload = dict(api_keys_doc.payload or {})
        key = str(payload.get("openaiApiKey") or "").strip()
        if key:
            return key

    any_doc = db.scalar(
        select(SocialDocument)
        .where(
            SocialDocument.workspace_uid == workspace_uid,
            SocialDocument.collection == APP_SETTINGS_COLLECTION,
        )
        .order_by(desc(SocialDocument.updated_at))
    )
    if any_doc is not None:
        payload = dict(any_doc.payload or {})
        key = str(payload.get("openaiApiKey") or "").strip()
        if key:
            return key

    raise ValueError("Workspace ayarlarinda OpenAI API key yok. PHP ayarlarindan API Keys kismina ekleyin.")


def _load_workspace_app_settings_payload(db: Session, workspace_uid: str) -> dict[str, Any]:
    api_keys_doc = db.scalar(
        select(SocialDocument).where(
            SocialDocument.workspace_uid == workspace_uid,
            SocialDocument.collection == APP_SETTINGS_COLLECTION,
            SocialDocument.doc_id == APP_SETTINGS_DOC_ID,
        )
    )
    if api_keys_doc is not None:
        return dict(api_keys_doc.payload or {})
    any_doc = db.scalar(
        select(SocialDocument)
        .where(
            SocialDocument.workspace_uid == workspace_uid,
            SocialDocument.collection == APP_SETTINGS_COLLECTION,
        )
        .order_by(desc(SocialDocument.updated_at))
    )
    return dict((any_doc.payload or {})) if any_doc is not None else {}


def _campaign_provider_settings(
    db: Session,
    workspace_uid: str,
    campaign_account_id: str | None = None,
) -> tuple[str, str]:
    account_id = str(campaign_account_id or "").strip()
    if account_id:
        account_doc = db.scalar(
            select(SocialDocument).where(
                SocialDocument.workspace_uid == workspace_uid,
                SocialDocument.collection == "campaign_accounts",
                SocialDocument.doc_id == account_id,
            )
        )
        if account_doc is None:
            raise HTTPException(status_code=404, detail="Kampanya hesabi bulunamadi.")
        account_payload = dict(account_doc.payload or {})
        account_base_url = _campaign_resolve_base_url(str(account_payload.get("campaignApiBaseUrl") or ""))
        account_api_key = str(account_payload.get("campaignApiKey") or "").strip()
        if not account_api_key:
            raise HTTPException(status_code=400, detail="Secili kampanya hesabinda Campaign API key yok.")
        return account_base_url, account_api_key

    payload = _load_workspace_app_settings_payload(db, workspace_uid)
    base_url = _campaign_resolve_base_url(str(payload.get("campaignApiBaseUrl") or ""))
    api_key = str(payload.get("campaignApiKey") or "").strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="Campaign API key ayarlanmamis. Kampanya yonetiminden secili hesap icin tanimlayin.")
    return base_url, api_key


def _campaign_api_is_sepetler_ai_v1(base_url: str) -> bool:
    norm = str(base_url or "").strip().rstrip("/").lower()
    return "/api/ai/v1" in norm


def _campaign_public_origin(base_url: str) -> str:
    parsed = urllib.parse.urlparse(str(base_url or "").strip())
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return str(base_url or "").strip().rstrip("/")


def _campaign_storage_public_url(base_url: str, folder: str, filename: str) -> str:
    name = str(filename or "").strip().lstrip("/")
    if not name:
        return ""
    if name.startswith(("http://", "https://")):
        return name
    folder_norm = str(folder or "").strip().strip("/")
    return f"{_campaign_public_origin(base_url)}/storage/app/public/{folder_norm}/{name}"


def _campaign_upstream_apply_auth(req: urllib.request.Request, *, base_url: str, api_key: str) -> None:
    if _campaign_api_is_sepetler_ai_v1(base_url):
        req.add_header("Authorization", f"Bearer {api_key}")
    else:
        req.add_header("x-api-key", api_key)


def _detect_outbound_ip() -> str | None:
    """Konteynerin/sunucunun public çıkış IP'sini api.ipify.org'dan saptar.
    OUTBOUND_IP_CACHE_TTL_SEC süresince cache'lenir; başarısızlıkta None döner.
    """
    global _outbound_ip_cache
    now = time.time()
    if _outbound_ip_cache is not None:
        ip, ts = _outbound_ip_cache
        if (now - ts) < OUTBOUND_IP_CACHE_TTL_SEC:
            return ip
    detect_url = (os.getenv("OUTBOUND_IP_DETECT_URL") or DEFAULT_OUTBOUND_IP_DETECT_URL).strip()
    req = urllib.request.Request(url=detect_url, method="GET")
    req.add_header("User-Agent", "Agent-Base-Campaign-Client/1.0")
    req.add_header("Accept", "text/plain, */*")
    try:
        with urllib.request.urlopen(req, timeout=4) as resp:
            ip = resp.read().decode("utf-8", errors="replace").strip()
        if ip:
            _outbound_ip_cache = (ip, now)
            return ip
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        social_media_logger.warning(
            "OUTBOUND_IP_DETECTION_FAILED url={} error={}",
            detect_url,
            str(exc),
        )
    return None


def _campaign_ensure_ip_registered(base_url: str, *, force: bool = False) -> None:
    """Sepetler API: kaynak IP'yi whitelist'e ekler. Bu çağrı yapılmadan
    /resources/stores, /banners gibi endpointler 401/403 döner.
    Aktif çıkış IP saptaması (ipify) yapılır; saptanan IP en son kaydedilenden
    farklıysa veya force=True ise yeniden kaydedilir.
    """
    if not _campaign_api_is_sepetler_ai_v1(base_url):
        return
    origin = _campaign_public_origin(base_url)
    if not origin:
        return
    current_ip = _detect_outbound_ip()
    last_registered = _campaign_ip_registration_cache.get(origin)
    if not force:
        if current_ip is not None and current_ip == last_registered:
            return
        if current_ip is None and last_registered:
            return
    url = f"{origin}/?ip_guncelle=dev"
    req = urllib.request.Request(url=url, method="GET")
    req.add_header("User-Agent", "Agent-Base-Campaign-Client/1.0")
    req.add_header("Accept", "*/*")
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            resp.read()
        if current_ip is not None:
            _campaign_ip_registration_cache[origin] = current_ip
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        social_media_logger.warning(
            "CAMPAIGN_IP_REGISTRATION_FAILED origin={} ip={} error={}",
            origin,
            current_ip or "unknown",
            str(exc),
        )


def _campaign_upstream_request(
    *,
    base_url: str,
    api_key: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    allow_statuses: set[int] | None = None,
) -> dict[str, Any]:
    _campaign_ensure_ip_registered(base_url)
    url = base_url + (path if path.startswith("/") else f"/{path}")
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")

    def _send() -> dict[str, Any]:
        req = urllib.request.Request(url=url, data=data, method=method.upper())
        _campaign_upstream_apply_auth(req, base_url=base_url, api_key=api_key)
        req.add_header("User-Agent", "Agent-Base-Campaign-Client/1.0")
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("content-type", "application/json")
        with urllib.request.urlopen(req, timeout=12) as resp:
            raw = resp.read().decode("utf-8", errors="replace").strip()
            if not raw:
                return {}
            try:
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, dict) else {"data": parsed}
            except json.JSONDecodeError:
                return {"raw": raw}

    try:
        return _send()
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403) and _campaign_api_is_sepetler_ai_v1(base_url):
            _campaign_ensure_ip_registered(base_url, force=True)
            try:
                return _send()
            except urllib.error.HTTPError as exc2:
                if allow_statuses and exc2.code in allow_statuses:
                    return {"status": exc2.code}
                raw = exc2.read().decode("utf-8", errors="replace").strip()
                detail = f"Campaign API HTTP {exc2.code}"
                if raw:
                    detail = f"{detail}: {raw[:400]}"
                raise HTTPException(status_code=502, detail=detail) from exc2
            except urllib.error.URLError as exc2:
                raise HTTPException(status_code=502, detail=f"Campaign API erisilemedi: {exc2.reason}") from exc2
        if allow_statuses and exc.code in allow_statuses:
            return {"status": exc.code}
        raw = exc.read().decode("utf-8", errors="replace").strip()
        detail = f"Campaign API HTTP {exc.code}"
        if raw:
            detail = f"{detail}: {raw[:400]}"
        raise HTTPException(status_code=502, detail=detail) from exc
    except urllib.error.URLError as exc:
        raise HTTPException(status_code=502, detail=f"Campaign API erisilemedi: {exc.reason}") from exc


def _sepetler_fetch_resource_list(
    *,
    base_url: str,
    api_key: str,
    resource: str,
    limit: int = 100,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    params: dict[str, str | int] = {"limit": limit, "direction": "desc"}
    while True:
        query = urllib.parse.urlencode(params)
        payload = _campaign_upstream_request(
            base_url=base_url,
            api_key=api_key,
            method="GET",
            path=f"/resources/{resource}?{query}",
        )
        batch = payload.get("data") if isinstance(payload.get("data"), list) else []
        for row in batch:
            if isinstance(row, dict):
                items.append(row)
        pagination = payload.get("pagination") if isinstance(payload.get("pagination"), dict) else {}
        if not pagination.get("has_more"):
            break
        next_cursor = pagination.get("next_cursor")
        if next_cursor is None:
            break
        params["cursor"] = next_cursor
    return items


def _normalize_sepetler_campaign_item(camp: dict[str, Any], *, base_url: str) -> dict[str, Any]:
    image_file = str(camp.get("image") or "").strip()
    media: list[str] = []
    if image_file:
        url = _campaign_storage_public_url(base_url, "campaign", image_file)
        if url:
            media.append(url)
    status_raw = camp.get("status")
    published = status_raw in (1, True, "1", "true", "active")
    dates_norm = _normalize_campaign_dates_for_catalog(camp)
    return {
        "id": str(camp.get("id") or "").strip(),
        "product": _campaign_first_str(
            camp.get("title"),
            camp.get("name"),
            camp.get("product"),
            camp.get("campaign_name"),
            str(camp.get("id") or "").strip(),
        ),
        "description": _campaign_first_str(
            camp.get("description"),
            camp.get("desc"),
            camp.get("body"),
            camp.get("summary"),
        ),
        "published": published,
        "pricing": _normalize_campaign_pricing_for_catalog(camp),
        "campaign_dates": dates_norm,
        "media": media,
        "redirect_url": _campaign_first_str(
            camp.get("redirect_url"),
            camp.get("redirectUrl"),
            camp.get("default_link"),
            camp.get("url"),
            camp.get("link"),
        ),
    }


def _parse_campaign_money(value: Any) -> float:
    try:
        return float(str(value or "0").replace(",", ".").strip())
    except (TypeError, ValueError):
        return 0.0


def _format_campaign_money(amount: float) -> str:
    rounded = round(float(amount), 2)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.2f}"


def _item_has_discount(item: dict[str, Any]) -> bool:
    return _parse_campaign_money(item.get("discount")) > 0


def _pricing_from_store_item(item: dict[str, Any]) -> dict[str, str]:
    price = _parse_campaign_money(item.get("price"))
    discount = _parse_campaign_money(item.get("discount"))
    discount_type = str(item.get("discount_type") or "amount").strip().lower()
    if price <= 0 or discount <= 0:
        return {}
    if discount_type == "percent":
        new_price = round(price * (1 - discount / 100), 2)
        discount_percent = _format_campaign_money(discount)
    else:
        new_price = max(0.0, round(price - discount, 2))
        discount_percent = _format_campaign_money((discount / price) * 100) if price else ""
    out = {
        "old_price": _format_campaign_money(price),
        "new_price": _format_campaign_money(new_price),
    }
    if discount_percent:
        out["discount_percent"] = discount_percent
    return out


def _normalize_sepetler_discounted_item(item: dict[str, Any], *, base_url: str) -> dict[str, Any]:
    item_id = str(item.get("id") or "").strip()
    image_file = str(item.get("image") or "").strip()
    media: list[str] = []
    if image_file:
        url = _campaign_storage_public_url(base_url, "product", image_file)
        if url:
            media.append(url)
    name = _campaign_first_str(item.get("name"), item.get("title"), item_id)
    pricing = _pricing_from_store_item(item)
    discount_type = str(item.get("discount_type") or "amount").strip().lower()
    discount_raw = _format_campaign_money(_parse_campaign_money(item.get("discount")))
    description_parts = [name]
    if pricing.get("old_price") and pricing.get("new_price"):
        if discount_type == "percent":
            description_parts.append(f"Indirim: %{discount_raw}")
        else:
            description_parts.append(f"Indirim: {discount_raw} TL")
        description_parts.append(f"Fiyat: {pricing['old_price']} -> {pricing['new_price']} TL")
    return {
        "id": item_id,
        "product": name,
        "description": " · ".join(description_parts),
        "published": item.get("status") in (1, True, "1", "true", "active"),
        "pricing": pricing,
        "campaign_dates": {},
        "media": media,
        "redirect_url": "",
        "source": "store_item",
        "store_item_id": item_id,
        "discount_type": discount_type,
    }


def _sepetler_fetch_items_for_store(
    *,
    base_url: str,
    api_key: str,
    store_id: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    store_key = str(store_id or "").strip()
    if not store_key.isdigit():
        return []
    items: list[dict[str, Any]] = []
    params: dict[str, str | int] = {"limit": limit, "direction": "desc", "store_id": store_key}
    while True:
        query = urllib.parse.urlencode(params)
        payload = _campaign_upstream_request(
            base_url=base_url,
            api_key=api_key,
            method="GET",
            path=f"/resources/items?{query}",
        )
        batch = payload.get("data") if isinstance(payload.get("data"), list) else []
        for row in batch:
            if isinstance(row, dict):
                items.append(row)
        pagination = payload.get("pagination") if isinstance(payload.get("pagination"), dict) else {}
        if not pagination.get("has_more"):
            break
        next_cursor = pagination.get("next_cursor")
        if next_cursor is None:
            break
        params["cursor"] = next_cursor
    return items


def _build_sepetler_store_discounted_products(*, base_url: str, api_key: str, store_id: str) -> list[dict[str, Any]]:
    items_raw = _sepetler_fetch_items_for_store(base_url=base_url, api_key=api_key, store_id=store_id)
    products: list[dict[str, Any]] = []
    for row in items_raw:
        if not _item_has_discount(row):
            continue
        products.append(_normalize_sepetler_discounted_item(row, base_url=base_url))
    return products


def _build_sepetler_campaign_catalog(*, base_url: str, api_key: str) -> list[dict[str, Any]]:
    """Sepetler /resources/stores: mağaza listesi. Her mağazanın `campaigns` listesi
    boş başlatılır — kullanıcı mağazayı UI'da seçtiğinde `/campaign/store-products`
    (indirimli ürünler) lazy load edilir.
    """
    stores_raw = _sepetler_fetch_resource_list(base_url=base_url, api_key=api_key, resource="stores")
    out: list[dict[str, Any]] = []
    for row in stores_raw:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("id") or "").strip()
        if not sid.isdigit():
            continue
        out.append(
            {
                "id": sid,
                "name": str(row.get("name") or "").strip() or f"Magaza {sid}",
                "campaigns": [],
            }
        )
    return out


def _sepetler_publish_campaign_banner(
    *,
    base_url: str,
    api_key: str,
    body: dict[str, Any],
    store_id: str,
    image_urls: list[str],
) -> dict[str, Any]:
    store_id_clean = str(store_id or "").strip()
    if not store_id_clean.isdigit():
        raise HTTPException(
            status_code=422,
            detail="Sepetler AI API yayini icin sayisal magaza (store) secimi gerekli.",
        )
    start_date = str(body.get("start_date") or "").strip() or None
    end_date = str(body.get("end_date") or "").strip() or None
    title = _campaign_first_str(
        body.get("campaign_name"),
        body.get("caption"),
        body.get("title"),
    ) or "Kampanya banner"
    banner_payload: dict[str, Any] = {
        "title": title[:191],
        "image_url": image_urls[0],
        "target_store_id": int(store_id_clean),
        "status": True,
    }
    if start_date:
        banner_payload["start_date"] = start_date
    if end_date:
        banner_payload["end_date"] = end_date
    redirect_url = str(body.get("redirect_url") or "").strip()
    if redirect_url:
        banner_payload["default_link"] = redirect_url
    return _campaign_upstream_request(
        base_url=base_url,
        api_key=api_key,
        method="POST",
        path="/banners",
        payload=banner_payload,
    )




def _campaign_first_str(*candidates: Any) -> str:
    for x in candidates:
        if x is None:
            continue
        s = str(x).strip()
        if s:
            return s
    return ""


def _normalize_campaign_pricing_for_catalog(camp: dict[str, Any]) -> dict[str, str]:
    """Map common upstream pricing keys to snake_case fields consumed by the PHP banner prompt."""
    raw = camp.get("pricing")
    p: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
    price_blob = camp.get("price") if isinstance(camp.get("price"), dict) else camp.get("prices")
    if isinstance(price_blob, dict):
        for k, v in price_blob.items():
            if k not in p and v is not None and str(v).strip():
                p[str(k)] = v
    old = _campaign_first_str(
        p.get("old_price"),
        p.get("oldPrice"),
        p.get("price_old"),
        p.get("previous_price"),
        p.get("list_price"),
        p.get("regular_price"),
        p.get("msrp"),
        camp.get("old_price"),
        camp.get("oldPrice"),
        camp.get("price_old"),
    )
    new = _campaign_first_str(
        p.get("new_price"),
        p.get("newPrice"),
        p.get("price_new"),
        p.get("sale_price"),
        p.get("current_price"),
        p.get("discounted_price"),
        p.get("final_price"),
        camp.get("new_price"),
        camp.get("newPrice"),
        camp.get("sale_price"),
    )
    disc_raw = _campaign_first_str(
        p.get("discount_percent"),
        p.get("discountPercent"),
        p.get("discount_pct"),
        p.get("discount"),
        p.get("percent_off"),
        p.get("pct_off"),
        camp.get("discount_percent"),
        camp.get("discountPercent"),
    )
    disc = disc_raw.replace("%", "").strip() if disc_raw else ""
    out: dict[str, str] = {}
    if old:
        out["old_price"] = old
    if new:
        out["new_price"] = new
    if disc:
        out["discount_percent"] = disc
    return out


def _normalize_campaign_dates_for_catalog(camp: dict[str, Any]) -> dict[str, str]:
    raw = camp.get("campaign_dates")
    d: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
    start = _campaign_first_str(
        d.get("start_date"),
        d.get("startDate"),
        d.get("from"),
        d.get("valid_from"),
        d.get("validFrom"),
        camp.get("start_date"),
        camp.get("startDate"),
        camp.get("date_start"),
        camp.get("starts_at"),
        camp.get("valid_from"),
    )
    end = _campaign_first_str(
        d.get("end_date"),
        d.get("endDate"),
        d.get("to"),
        d.get("valid_until"),
        d.get("validTo"),
        camp.get("end_date"),
        camp.get("endDate"),
        camp.get("date_end"),
        camp.get("ends_at"),
        camp.get("valid_until"),
    )
    out: dict[str, str] = {}
    if start:
        out["start_date"] = start
    if end:
        out["end_date"] = end
    return out


def _normalize_campaign_catalog_stores(stores_raw: Any) -> list[dict[str, Any]]:
    stores: list[dict[str, Any]] = []
    if not isinstance(stores_raw, list):
        return stores
    for row in stores_raw:
        if not isinstance(row, dict):
            continue
        campaigns_raw = row.get("campaigns")
        campaigns: list[dict[str, Any]] = []
        if isinstance(campaigns_raw, list):
            for camp in campaigns_raw:
                if not isinstance(camp, dict):
                    continue
                media = [str(x or "").strip() for x in list(camp.get("media") or []) if str(x or "").strip()]
                pricing_norm = _normalize_campaign_pricing_for_catalog(camp)
                dates_norm = _normalize_campaign_dates_for_catalog(camp)
                campaigns.append(
                    {
                        "id": str(camp.get("id") or "").strip(),
                        "product": _campaign_first_str(
                            camp.get("product"),
                            camp.get("name"),
                            camp.get("title"),
                            camp.get("campaign_name"),
                            str(camp.get("id") or "").strip(),
                        ),
                        "description": _campaign_first_str(
                            camp.get("description"),
                            camp.get("desc"),
                            camp.get("body"),
                            camp.get("summary"),
                            camp.get("product_description"),
                            camp.get("productDescription"),
                        ),
                        "published": bool(camp.get("published")),
                        "pricing": pricing_norm,
                        "campaign_dates": dates_norm,
                        "media": media,
                        "redirect_url": _campaign_first_str(
                            camp.get("redirect_url"),
                            camp.get("redirectUrl"),
                            camp.get("url"),
                            camp.get("link"),
                            camp.get("deeplink"),
                        ),
                    }
                )
        stores.append(
            {
                "id": str(row.get("id") or "").strip(),
                "name": str(row.get("name") or "").strip(),
                "campaigns": campaigns,
            }
        )
    return stores




def _safe_publish_time(value: str | None) -> str:
    candidate = (value or "").strip()
    if len(candidate) == 5 and candidate[2] == ":" and candidate.replace(":", "").isdigit():
        hh = int(candidate[:2])
        mm = int(candidate[3:])
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return candidate
    return "12:00"


def _template_for_store(template_id: str | None) -> dict[str, Any]:
    key = str(template_id or "welcome_minimal").strip()
    return dict(STORE_TEMPLATE_LIBRARY.get(key) or STORE_TEMPLATE_LIBRARY["welcome_minimal"])


def _append_automation_event(
    *,
    db: Session,
    workspace_uid: str,
    event_type: str,
    payload: dict[str, Any],
) -> str:
    event_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()
    db.add(
        SocialDocument(
            workspace_uid=workspace_uid,
            collection=AUTOMATION_EVENTS_COLLECTION,
            doc_id=event_id,
            payload={
                "eventType": event_type,
                "eventPayload": payload,
                "triggeredAt": now_iso,
                "createdAt": now_iso,
                "source": "store_workflow",
            },
        )
    )
    return event_id


def _store_row(db: Session, workspace_uid: str, store_id: str) -> SocialDocument | None:
    return db.scalar(
        select(SocialDocument).where(
            SocialDocument.workspace_uid == workspace_uid,
            SocialDocument.collection == STORES_RUNTIME_COLLECTION,
            SocialDocument.doc_id == store_id,
        )
    )


def _create_store_workflow_and_schedule(
    *,
    db: Session,
    workspace_uid: str,
    store_id: str,
    store_payload: dict[str, Any],
    openai_key: str,
    override_delay_days: int | None = None,
) -> dict[str, Any]:
    template = _template_for_store(str(store_payload.get("selected_template_id") or "welcome_minimal"))
    delay_days = max(0, min(30, int(override_delay_days if override_delay_days is not None else 3)))
    scheduled_dt = (datetime.now(timezone.utc) + timedelta(days=delay_days)).replace(minute=0, second=0, microsecond=0)
    publish_date = scheduled_dt.date().isoformat()
    publish_time = scheduled_dt.strftime("%H:%M")

    account_name = str(store_payload.get("name") or "Store").strip() or "Store"
    instagram_handle = str(store_payload.get("instagram_handle") or "").strip()
    template_brief = (
        f"Store approved welcome post. Store: {account_name}. Handle: {instagram_handle or 'unknown'}. "
        f"Template layout: {template.get('layout')}. Text slots: {', '.join(template.get('text_slots') or [])}. "
        f"CTA style: {template.get('cta_style')}. Visual direction: {template.get('visual_direction')}. "
        f"Color approach: {template.get('color_approach')}. "
        "Write an Instagram hoş geldin postu in Turkish with clear CTA."
    )
    caption = generate_caption(template_brief, "samimi", openai_api_key=openai_key)
    image_prompt = (
        f"Instagram welcome campaign visual for {account_name}. "
        f"Respect template layout: {template.get('layout')}. "
        f"Use visual direction: {template.get('visual_direction')}. "
        f"Color approach: {template.get('color_approach')}. "
        "No random text artifacts."
    )
    image_result = generate_images(
        prompt=image_prompt,
        count=1,
        openai_api_key=openai_key,
        platform="feed",
    )
    image_url = str((image_result[0] or {}).get("url") or "").strip() if image_result else ""
    if not image_url:
        raise RuntimeError("Store workflow gorsel uretemedi.")

    workflow_id = str(uuid.uuid4())
    scheduled_post_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()
    workflow_doc = {
        "workflow_type": "store_approval_welcome_instagram",
        "store_id": store_id,
        "scheduled_for": scheduled_dt.isoformat(),
        "status": "scheduled",
        "cancellation_policy": "cancel_if_store_rejected_before_publish",
        "template_id": template.get("id"),
        "scheduled_post_id": scheduled_post_id,
        "created_at": now_iso,
        "updated_at": now_iso,
    }
    post_doc = {
        "accountId": store_id,
        "accountName": account_name,
        "date": publish_date,
        "time": publish_time,
        "scheduledAt": scheduled_dt.isoformat(),
        "scheduled_at": scheduled_dt.isoformat(),
        "prompt": image_prompt,
        "caption": caption,
        "imageUrl": image_url,
        "imageUrls": [image_url],
        "publishStatus": "pending",
        "approvalStatus": "approved",
        "status": "scheduled",
        "publishTargets": {"instagram_post": True, "instagram_story": False, "facebook_post": False},
        "source": "store_workflow",
        "storeId": store_id,
        "automationWorkflowId": workflow_id,
        "templateId": template.get("id"),
        "templateSnapshot": template,
        "createdAt": now_iso,
    }
    db.add(
        SocialDocument(
            workspace_uid=workspace_uid,
            collection=AUTOMATION_WORKFLOWS_COLLECTION,
            doc_id=workflow_id,
            payload=workflow_doc,
        )
    )
    db.add(
        SocialDocument(
            workspace_uid=workspace_uid,
            collection=SCHEDULED_POSTS_COLLECTION,
            doc_id=scheduled_post_id,
            payload=post_doc,
        )
    )
    _append_automation_event(
        db=db,
        workspace_uid=workspace_uid,
        event_type="automation_triggered",
        payload={
            "store_id": store_id,
            "workflow_id": workflow_id,
            "scheduled_for": scheduled_dt.isoformat(),
            "template_id": template.get("id"),
        },
    )
    _append_automation_event(
        db=db,
        workspace_uid=workspace_uid,
        event_type="asset_generated",
        payload={"store_id": store_id, "workflow_id": workflow_id, "image_url": image_url},
    )
    _append_automation_event(
        db=db,
        workspace_uid=workspace_uid,
        event_type="scheduled_post_created",
        payload={
            "store_id": store_id,
            "workflow_id": workflow_id,
            "scheduled_post_id": scheduled_post_id,
            "scheduled_at": scheduled_dt.isoformat(),
        },
    )
    return {
        "workflow_id": workflow_id,
        "scheduled_post_id": scheduled_post_id,
        "scheduled_for": scheduled_dt.isoformat(),
        "caption": caption,
        "image_url": image_url,
        "template": template,
    }




def _clamp_delay_days(value: int | str | None, default: int = 3) -> int:
    try:
        n = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        n = default
    return max(0, min(365, n))


def _infer_delay_days_from_text(message: str, default: int = 3) -> int:
    m = re.search(r"(\d+)\s*g[üu]n", message.lower())
    if not m:
        return default
    return _clamp_delay_days(m.group(1), default=default)


def _fallback_chat_interpretation(message: str) -> dict:
    txt = (message or "").strip()
    delay_days = _infer_delay_days_from_text(txt, default=3)
    return {
        "event_type": "chat_prompt",
        "delay_days": delay_days,
        "publish_time": "12:00",
        "caption_topic": txt,
        "image_prompt": f"{txt} icin premium sosyal medya post gorseli",
        "account_name": "",
        "account_id": "",
        "instagram_post": True,
        "instagram_story": False,
        "facebook_post": False,
        "approval_required": True,
    }


@router.post("/agents")
def create_agent(body: AgentCreateRequest):
    try:
        manager_service.seed_defaults_if_missing()
        return manager_service.create_agent(body.model_dump())
    except Exception as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})


@router.get("/agents")
def list_agents():
    try:
        manager_service.seed_defaults_if_missing()
        return {"items": manager_service.list_agents()}
    except Exception as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})


@router.get("/agents/{agent_id}")
def get_agent(agent_id: str):
    try:
        manager_service.seed_defaults_if_missing()
        item = manager_service.get_agent(agent_id)
        if not item:
            return JSONResponse(status_code=404, content={"error": "Agent bulunamadi."})
        return item
    except Exception as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})


@router.patch("/agents/{agent_id}")
def update_agent(agent_id: str, body: AgentUpdateRequest):
    try:
        updated = manager_service.update_agent(agent_id, body.model_dump(exclude_unset=True))
        if not updated:
            return JSONResponse(status_code=404, content={"error": "Agent bulunamadi."})
        return updated
    except Exception as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})


@router.post("/agents/{agent_id}/run")
def run_agent(agent_id: str, body: AgentRunRequest):
    try:
        return runtime_service.run_agent(agent_id, body.message, gemini_api_key=body.gemini_api_key)
    except Exception as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})


@router.post("/manager/run")
def manager_run(body: ManagerRunRequest):
    try:
        return runtime_service.manager_run(
            message=body.message,
            agent_id=body.agent_id,
            gemini_api_key=body.gemini_api_key,
        )
    except Exception as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})


@router.post("/automation/stores/fake-create")
def automation_store_fake_create(
    body: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        name = str((body or {}).get("name") or "").strip()
        if not name:
            return JSONResponse(status_code=400, content={"error": "Store name gerekli."})
        store_id = str((body or {}).get("id") or f"store_{uuid.uuid4().hex[:8]}")
        if _store_row(db, user.workspace_uid, store_id) is not None:
            return JSONResponse(status_code=409, content={"error": "Store id zaten var."})
        now_iso = datetime.now(timezone.utc).isoformat()
        selected_template_id = str((body or {}).get("selected_template_id") or "welcome_minimal").strip() or "welcome_minimal"
        store_doc = {
            "id": store_id,
            "name": name,
            "status": "pending_approval",
            "created_at": now_iso,
            "approved_at": "",
            "rejected_at": "",
            "instagram_handle": str((body or {}).get("instagram_handle") or "").strip(),
            "selected_template_id": selected_template_id,
        }
        db.add(
            SocialDocument(
                workspace_uid=user.workspace_uid,
                collection=STORES_RUNTIME_COLLECTION,
                doc_id=store_id,
                payload=store_doc,
            )
        )
        _append_automation_event(
            db=db,
            workspace_uid=user.workspace_uid,
            event_type="store_created",
            payload={"store_id": store_id, "status": "pending_approval"},
        )
        db.commit()
        return {"store": store_doc}
    except Exception as exc:
        _log_api_error(endpoint="/social-media/automation/stores/fake-create", exc=exc, payload=body or {})
        return JSONResponse(status_code=400, content={"error": str(exc)})


@router.get("/automation/stores")
def automation_store_list(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = db.scalars(
        select(SocialDocument)
        .where(
            SocialDocument.workspace_uid == user.workspace_uid,
            SocialDocument.collection == STORES_RUNTIME_COLLECTION,
        )
        .order_by(desc(SocialDocument.updated_at))
    ).all()
    items: list[dict[str, Any]] = []
    for row in rows:
        payload = dict(row.payload or {})
        payload.setdefault("id", row.doc_id)
        items.append(payload)
    return {"items": items, "templates": list(STORE_TEMPLATE_LIBRARY.values())}


@router.post("/automation/stores/{store_id}/approve")
def automation_store_approve(
    store_id: str,
    body: dict | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        row = _store_row(db, user.workspace_uid, store_id)
        if row is None:
            return JSONResponse(status_code=404, content={"error": "Store bulunamadi."})
        payload = dict(row.payload or {})
        now_iso = datetime.now(timezone.utc).isoformat()
        payload["status"] = "approved"
        payload["approved_at"] = now_iso
        payload["rejected_at"] = ""
        if isinstance(body, dict) and body.get("selected_template_id"):
            payload["selected_template_id"] = str(body.get("selected_template_id") or payload.get("selected_template_id") or "welcome_minimal")
        row.payload = payload
        existing = db.scalars(
            select(SocialDocument).where(
                SocialDocument.workspace_uid == user.workspace_uid,
                SocialDocument.collection == AUTOMATION_WORKFLOWS_COLLECTION,
            )
        ).all()
        for wf_row in existing:
            wf = dict(wf_row.payload or {})
            if str(wf.get("store_id") or "") != store_id:
                continue
            if str(wf.get("status") or "") in {"scheduled", "running"}:
                return JSONResponse(
                    status_code=409,
                    content={"error": "Store icin aktif workflow zaten var.", "workflow_id": wf_row.doc_id},
                )
        _append_automation_event(
            db=db,
            workspace_uid=user.workspace_uid,
            event_type="store_approved",
            payload={"store_id": store_id, "approved_at": now_iso},
        )
        openai_key = _resolve_workspace_openai_key(db, user.workspace_uid, None)
        wf = _create_store_workflow_and_schedule(
            db=db,
            workspace_uid=user.workspace_uid,
            store_id=store_id,
            store_payload=payload,
            openai_key=openai_key,
            override_delay_days=int(body.get("delay_days")) if isinstance(body, dict) and body.get("delay_days") is not None else None,
        )
        db.commit()
        return {"store": payload, "workflow": wf}
    except Exception as exc:
        _log_api_error(endpoint=f"/social-media/automation/stores/{store_id}/approve", exc=exc, payload=body or {})
        return JSONResponse(status_code=400, content={"error": str(exc)})


@router.post("/automation/stores/{store_id}/reject")
def automation_store_reject(
    store_id: str,
    body: dict | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        row = _store_row(db, user.workspace_uid, store_id)
        if row is None:
            return JSONResponse(status_code=404, content={"error": "Store bulunamadi."})
        now_iso = datetime.now(timezone.utc).isoformat()
        payload = dict(row.payload or {})
        payload["status"] = "rejected"
        payload["rejected_at"] = now_iso
        row.payload = payload
        reason = str((body or {}).get("reason") or "store_rejected").strip() or "store_rejected"
        cancelled_workflows = 0
        cancelled_posts = 0
        wf_rows = db.scalars(
            select(SocialDocument).where(
                SocialDocument.workspace_uid == user.workspace_uid,
                SocialDocument.collection == AUTOMATION_WORKFLOWS_COLLECTION,
            )
        ).all()
        for wf_row in wf_rows:
            wf = dict(wf_row.payload or {})
            if str(wf.get("store_id") or "") != store_id:
                continue
            if str(wf.get("status") or "") in {"published", "cancelled"}:
                continue
            wf["status"] = "cancelled"
            wf["cancelled_at"] = now_iso
            wf["cancel_reason"] = reason
            wf_row.payload = wf
            cancelled_workflows += 1
            sp_id = str(wf.get("scheduled_post_id") or "").strip()
            if sp_id:
                sp_row = db.scalar(
                    select(SocialDocument).where(
                        SocialDocument.workspace_uid == user.workspace_uid,
                        SocialDocument.collection == SCHEDULED_POSTS_COLLECTION,
                        SocialDocument.doc_id == sp_id,
                    )
                )
                if sp_row is not None:
                    sp = dict(sp_row.payload or {})
                    if str(sp.get("publishStatus") or "") != "published":
                        sp["status"] = "cancelled"
                        sp["publishStatus"] = "failed"
                        sp["approvalStatus"] = "rejected"
                        sp["cancelReason"] = reason
                        sp["cancelledAt"] = now_iso
                        sp_row.payload = sp
                        cancelled_posts += 1
                        _append_automation_event(
                            db=db,
                            workspace_uid=user.workspace_uid,
                            event_type="workflow_cancelled",
                            payload={
                                "store_id": store_id,
                                "workflow_id": wf_row.doc_id,
                                "scheduled_post_id": sp_id,
                                "reason": reason,
                            },
                        )
        _append_automation_event(
            db=db,
            workspace_uid=user.workspace_uid,
            event_type="store_rejected",
            payload={"store_id": store_id, "rejected_at": now_iso, "reason": reason},
        )
        db.commit()
        return {
            "store": payload,
            "cancelled_workflows": cancelled_workflows,
            "cancelled_posts": cancelled_posts,
        }
    except Exception as exc:
        _log_api_error(endpoint=f"/social-media/automation/stores/{store_id}/reject", exc=exc, payload=body or {})
        return JSONResponse(status_code=400, content={"error": str(exc)})


@router.get("/automation/workflows")
def automation_workflows(
    store_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = db.scalars(
        select(SocialDocument)
        .where(
            SocialDocument.workspace_uid == user.workspace_uid,
            SocialDocument.collection == AUTOMATION_WORKFLOWS_COLLECTION,
        )
        .order_by(desc(SocialDocument.updated_at))
    ).all()
    out: list[dict[str, Any]] = []
    for row in rows:
        payload = dict(row.payload or {})
        payload.setdefault("id", row.doc_id)
        if store_id and str(payload.get("store_id") or "") != str(store_id):
            continue
        out.append(payload)
    return {"items": out}


@router.post("/automation/workflows/{workflow_id}/dispatch-publish")
def automation_dispatch_publish(
    workflow_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        wf_row = db.scalar(
            select(SocialDocument).where(
                SocialDocument.workspace_uid == user.workspace_uid,
                SocialDocument.collection == AUTOMATION_WORKFLOWS_COLLECTION,
                SocialDocument.doc_id == workflow_id,
            )
        )
        if wf_row is None:
            return JSONResponse(status_code=404, content={"error": "Workflow bulunamadi."})
        wf = dict(wf_row.payload or {})
        if str(wf.get("status") or "") in {"cancelled", "published"}:
            return {"workflow": {"id": workflow_id, **wf}, "dispatch": "noop"}
        store_id = str(wf.get("store_id") or "")
        store = _store_row(db, user.workspace_uid, store_id)
        if store is None or str((store.payload or {}).get("status") or "") == "rejected":
            wf["status"] = "cancelled"
            wf["cancelled_at"] = datetime.now(timezone.utc).isoformat()
            wf["cancel_reason"] = "store_rejected_before_publish"
            wf_row.payload = wf
            sp_id = str(wf.get("scheduled_post_id") or "").strip()
            if sp_id:
                sp_row = db.scalar(
                    select(SocialDocument).where(
                        SocialDocument.workspace_uid == user.workspace_uid,
                        SocialDocument.collection == SCHEDULED_POSTS_COLLECTION,
                        SocialDocument.doc_id == sp_id,
                    )
                )
                if sp_row is not None:
                    sp = dict(sp_row.payload or {})
                    sp["status"] = "cancelled"
                    sp["publishStatus"] = "failed"
                    sp["approvalStatus"] = "rejected"
                    sp["cancelReason"] = "store_rejected_before_publish"
                    sp_row.payload = sp
            _append_automation_event(
                db=db,
                workspace_uid=user.workspace_uid,
                event_type="workflow_cancelled",
                payload={"store_id": store_id, "workflow_id": workflow_id, "reason": "store_rejected_before_publish"},
            )
            db.commit()
            return {"workflow": {"id": workflow_id, **wf}, "dispatch": "cancelled"}
        scheduled_for = str(wf.get("scheduled_for") or "").strip()
        try:
            scheduled_dt = datetime.fromisoformat(scheduled_for.replace("Z", "+00:00"))
        except Exception:
            scheduled_dt = datetime.now(timezone.utc)
        if scheduled_dt > datetime.now(timezone.utc):
            return {"workflow": {"id": workflow_id, **wf}, "dispatch": "not_due"}
        sp_id = str(wf.get("scheduled_post_id") or "").strip()
        sp_row = None
        if sp_id:
            sp_row = db.scalar(
                select(SocialDocument).where(
                    SocialDocument.workspace_uid == user.workspace_uid,
                    SocialDocument.collection == SCHEDULED_POSTS_COLLECTION,
                    SocialDocument.doc_id == sp_id,
                )
            )
        if sp_row is None:
            return JSONResponse(status_code=404, content={"error": "Scheduled post bulunamadi."})
        sp = dict(sp_row.payload or {})
        sp["publishStatus"] = "published"
        sp["status"] = "published"
        sp["publishedAt"] = datetime.now(timezone.utc).isoformat()
        sp_row.payload = sp
        wf["status"] = "published"
        wf["published_at"] = datetime.now(timezone.utc).isoformat()
        wf_row.payload = wf
        _append_automation_event(
            db=db,
            workspace_uid=user.workspace_uid,
            event_type="publish_completed",
            payload={"store_id": store_id, "workflow_id": workflow_id, "scheduled_post_id": sp_id},
        )
        db.commit()
        return {"workflow": {"id": workflow_id, **wf}, "scheduled_post_id": sp_id, "dispatch": "published"}
    except Exception as exc:
        _log_api_error(endpoint=f"/social-media/automation/workflows/{workflow_id}/dispatch-publish", exc=exc, payload={})
        return JSONResponse(status_code=400, content={"error": str(exc)})




@router.get("/usage/summary")
def usage_summary(
    account_id: int | None = None,
    days: int = 90,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """OpenAI/FAL üretim maliyetlerinin gün/ay/hesap/tür kırılımlı özeti."""
    from app.services import usage_service

    return usage_service.usage_summary(
        db,
        user_id=int(user.id),
        account_id=account_id,
        days=max(1, min(int(days or 90), 365)),
    )


@router.get("/usage/cost")
def usage_cost(
    post_id: str | None = None,
    draft_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Belirli bir post veya draft için biriken üretim maliyeti."""
    from app.services import usage_service

    if post_id:
        return {"cost_usd": round(usage_service.cost_for_post(db, user_id=int(user.id), post_id=post_id), 4)}
    if draft_id:
        return {"cost_usd": round(usage_service.cost_for_draft(db, user_id=int(user.id), draft_id=draft_id), 4)}
    return {"cost_usd": 0.0}


@router.post("/caption/generate")
def caption_generate(
    body: CaptionRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from app.services import usage_service
    from app.services.content_service import _last_caption_usage

    try:
        result = _sync_caption_generate_task(
            body.konu,
            body.tone,
            body.openai_api_key or body.gemini_api_key,
        )
        usage = _last_caption_usage()
        if usage and usage.get("model"):
            cost = usage_service.log_usage(
                db,
                user_id=int(user.id),
                kind="caption",
                model=str(usage["model"]),
                input_tokens=int(usage.get("input_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
            )
            (result or {}).setdefault("cost_usd", round(cost, 4))
        return result or {}
    except Exception as exc:
        _log_api_error(
            endpoint="/social-media/caption/generate",
            exc=exc,
            payload={"konu_len": len(body.konu or ""), "tone": body.tone, "has_openai_key": bool((body.openai_api_key or body.gemini_api_key or "").strip())},
        )
        return JSONResponse(status_code=400, content={"error": str(exc)})


@router.post("/caption/revize")
def caption_revize(
    body: RevizeRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from app.services import usage_service
    from app.services.content_service import _last_caption_usage

    try:
        result = _sync_caption_revize_task(
            body.mevcut_caption,
            body.revize_talebi,
            body.openai_api_key or body.gemini_api_key,
        )
        usage = _last_caption_usage()
        if usage and usage.get("model"):
            cost = usage_service.log_usage(
                db,
                user_id=int(user.id),
                kind="caption_revize",
                model=str(usage["model"]),
                input_tokens=int(usage.get("input_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
            )
            (result or {}).setdefault("cost_usd", round(cost, 4))
        return result or {}
    except Exception as exc:
        _log_api_error(
            endpoint="/social-media/caption/revize",
            exc=exc,
            payload={
                "caption_len": len(body.mevcut_caption or ""),
                "feedback_len": len(body.revize_talebi or ""),
                "has_openai_key": bool((body.openai_api_key or body.gemini_api_key or "").strip()),
            },
        )
        return JSONResponse(status_code=400, content={"error": str(exc)})


@router.post("/holiday/generate")
def holiday_generate(body: HolidayGenerateRequest):
    """Generate holiday-specific caption + image using GPT-4o.

    Step 1: GPT-4o crafts a warm, on-brand caption AND a detailed visual prompt in one shot.
    Step 2: The visual prompt is used to produce an Instagram-ready image.
    """
    try:
        dispatched = dispatch(
            "holiday_generate",
            _sync_holiday_generate_task,
            body.holiday_name,
            body.date_key,
            body.locale or "tr",
            body.openai_api_key,
            body.fal_api_key,
            body.generate_image,
            bool(body.generate_video),
            (body.extra_instructions or "").strip() or None,
        )
        if dispatched.get("queued"):
            return {"queued": True, "task_id": dispatched["task_id"], "status": "pending"}
        result = dispatched.get("result") or {}
        return HolidayGenerateResponse(**result)
    except (ValueError, RuntimeError) as exc:
        _log_api_error(endpoint="/social-media/holiday/generate", exc=exc)
        raise ValueError(str(exc)) from exc


@router.post("/video/generate")
def video_generate(
    body: VideoGenerateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Text-to-video or image-to-video (single reference URL) via fal.ai; stores result in configured storage (R2/local)."""
    from app.services import usage_service

    try:
        img = (body.image_url or "").strip() or None
        dispatched = dispatch(
            "video_generate",
            _sync_video_generate_task,
            (body.prompt or "").strip(),
            body.fal_api_key,
            img,
            int(body.duration_sec),
            bool(body.generate_audio),
        )
        if dispatched.get("queued"):
            return {"queued": True, "task_id": dispatched["task_id"], "status": "pending"}
        result = dispatched.get("result") or {}
        cost = usage_service.log_usage(
            db,
            user_id=int(user.id),
            kind="video",
            model="fal-kling-v3",
            seconds=float(body.duration_sec or 0),
        )
        if isinstance(result, dict):
            result.setdefault("cost_usd", round(cost, 4))
        return VideoGenerateResponse(**result) if isinstance(result, dict) else result
    except (ValueError, RuntimeError) as exc:
        _log_api_error(endpoint="/social-media/video/generate", exc=exc, payload={"prompt_len": len(body.prompt or "")})
        raise ValueError(str(exc)) from exc


@router.post("/flow/analyze")
def flow_analyze(body: AnalyzeRequest):
    """Analyse prompt + optional reference image; returns ContentContext.
    Synchronous — fast enough for immediate UI feedback.
    """
    try:
        ctx = _cis.analyze(
            user_prompt=body.prompt,
            reference_image_url=body.reference_image_url,
            platform=body.platform,
            openai_api_key=body.openai_api_key or body.gemini_api_key,
        )
        return ctx.model_dump()
    except Exception as exc:
        _log_api_error(
            endpoint="/social-media/flow/analyze",
            exc=exc,
            payload={"prompt_len": len(body.prompt or ""), "platform": body.platform},
        )
        return JSONResponse(status_code=400, content={"error": str(exc)})


@router.post("/flow/generate-images")
def flow_generate_images(
    body: ImageGenerateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Generate images — async (Celery task) when USE_CELERY=true, otherwise sync."""
    from app.services import usage_service

    try:
        platform = getattr(body, "platform", "feed") or "feed"
        reference_image_url = getattr(body, "reference_image_url", None)

        output_size = (getattr(body, "output_size", None) or getattr(body, "banner_size", None) or "").strip() or None
        dispatched = dispatch(
            "generate_images",
            _sync_generate_images_task,
            body.prompt,
            body.count,
            platform,
            reference_image_url,
            body.fal_api_key or body.gemini_api_key,
            body.openai_api_key,
            bool(getattr(body, "use_gpt", False)),
            reference_image_urls=body.reference_image_urls,
            output_size=output_size,
        )
        if dispatched.get("queued"):
            return {"queued": True, "task_id": dispatched["task_id"], "status": "pending"}
        images = (dispatched.get("result") or [])
        cost = usage_service.log_usage(
            db,
            user_id=int(user.id),
            kind="image",
            model="gpt-image-1",
            image_count=len(images) or body.count,
        )
        return {"session_id": str(uuid.uuid4())[:10], "images": images, "cost_usd": round(cost, 4)}
    except Exception as exc:
        _log_api_error(
            endpoint="/social-media/flow/generate-images",
            exc=exc,
            payload={"prompt_len": len(body.prompt or ""), "count": body.count, "has_fal_key": bool((body.fal_api_key or body.gemini_api_key or "").strip())},
        )
        return JSONResponse(status_code=400, content={"error": str(exc)})


@router.post("/flow/generate-from-reference")
def flow_generate_from_reference(
    body: ImageReferenceGenerateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from app.services import usage_service

    try:
        dispatched = dispatch(
            "generate_from_reference",
            _sync_generate_from_reference_task,
            body.reference_image_url,
            body.prompt,
            body.count,
            body.fal_api_key or body.gemini_api_key,
            body.openai_api_key,
            body.mode,
            body.reference_image_urls,
            body.skip_professionalization,
            body.output_size or body.banner_size,
        )
        if dispatched.get("queued"):
            return {"queued": True, "task_id": dispatched["task_id"], "status": "pending"}
        payload = dispatched.get("result") or {}
        images = payload.get("images") if isinstance(payload, dict) else []
        n = len(images) if isinstance(images, list) else (body.count or 1)
        cost = usage_service.log_usage(
            db,
            user_id=int(user.id),
            kind="image_reference",
            model="gpt-image-1",
            image_count=n,
        )
        if isinstance(payload, dict):
            payload.setdefault("cost_usd", round(cost, 4))
        return payload
    except Exception as exc:
        _log_api_error(
            endpoint="/social-media/flow/generate-from-reference",
            exc=exc,
            payload={
                "prompt_len": len(body.prompt or ""),
                "count": body.count,
                "reference_head": (body.reference_image_url or "")[:120],
                "has_fal_key": bool((body.fal_api_key or body.gemini_api_key or "").strip()),
            },
        )
        return JSONResponse(status_code=400, content={"error": str(exc)})


@router.post("/flow/revise-image")
def flow_revise_image(
    body: ImageReviseRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Revise image — async (Celery task) when USE_CELERY=true, otherwise sync."""
    from app.services import usage_service

    try:
        platform = getattr(body, "platform", "feed") or "feed"

        dispatched = dispatch(
            "revise_image",
            _sync_revise_image_task,
            body.image_url,
            body.feedback,
            body.count,
            platform,
            body.fal_api_key or body.gemini_api_key,
            body.openai_api_key,
            reference_image_urls=body.reference_image_urls,
            output_size=body.output_size or body.banner_size,
            revision_context=body.revision_context,
        )
        if dispatched.get("queued"):
            return {"queued": True, "task_id": dispatched["task_id"], "status": "pending"}
        images = (dispatched.get("result") or [])
        cost = usage_service.log_usage(
            db,
            user_id=int(user.id),
            kind="image_revise",
            model="gpt-image-1",
            image_count=len(images) or body.count or 1,
        )
        return {"session_id": str(uuid.uuid4())[:10], "images": images, "cost_usd": round(cost, 4)}
    except Exception as exc:
        _log_api_error(
            endpoint="/social-media/flow/revise-image",
            exc=exc,
            payload={
                "feedback_len": len(body.feedback or ""),
                "count": body.count,
                "image_url_head": (body.image_url or "")[:120],
                "has_fal_key": bool((body.fal_api_key or body.gemini_api_key or "").strip()),
            },
        )
        return JSONResponse(status_code=400, content={"error": str(exc)})


@router.get("/tasks/{task_id}")
def get_task_status(task_id: str):
    """Poll Celery task status.

    Her zaman 200 + TaskStatusResponse doner; boylece istemci `fetch` hata firtlatmaz.
    Celery kapali veya broker hatasi: ``status=failure`` ve ``error`` aciklamasi (UI sonsuz beklemesin).
    """
    if not use_celery():
        return TaskStatusResponse(
            task_id=task_id,
            status="failure",
            result=None,
            error=(
                "Celery kapali (USE_CELERY). Arka plan kuyrugu yok — .env icinde USE_CELERY=true "
                "ve REDIS_URL ayarlayin; Docker'da worker ayri surec olarak calismali."
            ),
            progress=0,
        )
    try:
        from app.core.celery_app import celery_app as _celery_app

        result = _celery_app.AsyncResult(task_id)
        status_raw = (result.status or "PENDING").lower()
        # Normalise to TaskStatusResponse literals
        status_map = {
            "pending": "pending",
            "started": "started",
            "success": "success",
            "failure": "failure",
            "retry": "retry",
        }
        status = status_map.get(status_raw, "pending")
        progress = 0
        if isinstance(result.info, dict):
            progress = int(result.info.get("progress", 0))

        return TaskStatusResponse(
            task_id=task_id,
            status=status,
            result=result.result if result.successful() else None,
            error=str(result.result) if result.failed() else None,
            progress=progress,
        )
    except Exception as exc:
        return TaskStatusResponse(
            task_id=task_id,
            status="failure",
            result=None,
            error=str(exc),
            progress=0,
        )


@router.post("/flow/session/start")
def flow_session_start(body: FlowSessionStartRequest):
    try:
        return social_flow.generate_candidates(
            prompt=body.prompt,
            count=body.count,
            gemini_api_key=body.gemini_api_key,
        )
    except Exception as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})


@router.post("/flow/session/feedback")
def flow_session_feedback(body: FlowSessionFeedbackRequest):
    try:
        return social_flow.revise_selected(
            session_id=body.session_id,
            selected_image_url=body.selected_image_url,
            feedback=body.feedback,
            revised_count=body.revised_count,
            gemini_api_key=body.gemini_api_key,
        )
    except Exception as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})


@router.get("/flow/session/{session_id}")
def flow_session_get(session_id: str):
    session = social_flow.get_session(session_id)
    if not session:
        return JSONResponse(status_code=404, content={"error": "Session bulunamadi."})
    return session


@router.get("/campaign/catalog")
def campaign_catalog(
    campaign_account_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ws_key = str(user.workspace_uid or "").strip() or "__unknown__"
    account_key = str(campaign_account_id or "workspace").strip() or "workspace"
    has_campaign_account = account_key != "workspace"
    cache_key = f"{ws_key}:{account_key}"
    now_ts = time.time()
    cached = _campaign_catalog_cache.get(cache_key)
    if cached is not None:
        age = now_ts - float(cached.get("ts", 0.0))
        cached_provider = cached.get("provider") if isinstance(cached.get("provider"), dict) else {}
        if str(cached_provider.get("source") or "") != "example_py_fallback" and 0 <= age < CAMPAIGN_CATALOG_SERVER_CACHE_TTL_SEC:
            return {
                "stores": cached.get("stores", []),
                "campaigns": cached.get("campaigns", []),
                "provider": cached["provider"],
                "cached": True,
            }

    base_url = ""
    provider: dict[str, Any] = {"base_url": base_url, "source": "upstream"}
    upstream_error = ""
    stores: list[dict[str, Any]] = []
    campaigns: list[dict[str, Any]] = []
    try:
        base_url, api_key = _campaign_provider_settings(db, user.workspace_uid, campaign_account_id)
        provider = {
            "base_url": base_url,
            "source": "sepetler_ai_v1" if _campaign_api_is_sepetler_ai_v1(base_url) else "upstream",
        }
        if _campaign_api_is_sepetler_ai_v1(base_url):
            stores = _build_sepetler_campaign_catalog(base_url=base_url, api_key=api_key)
            campaigns = []
        else:
            payload = _campaign_upstream_request(
                base_url=base_url,
                api_key=api_key,
                method="GET",
                path="/stores",
            )
            stores = _normalize_campaign_catalog_stores(payload.get("stores") if isinstance(payload, dict) else [])
            campaigns = []
    except HTTPException as exc:
        upstream_error = str(exc.detail or "")

    if not stores and not campaigns and cached is not None:
        cached_provider = cached.get("provider") if isinstance(cached.get("provider"), dict) else {}
        if has_campaign_account and str(cached_provider.get("source") or "") == "example_py_fallback":
            cached = None

    if not stores and not campaigns and cached is not None:
        return {
            "stores": cached.get("stores", []),
            "campaigns": cached.get("campaigns", []),
            "provider": cached["provider"],
            "cached": True,
            "stale": True,
            "upstream_error": upstream_error,
        }

    _campaign_catalog_cache[cache_key] = {
        "ts": now_ts,
        "stores": stores,
        "campaigns": campaigns,
        "provider": provider,
    }
    return {
        "stores": stores,
        "campaigns": campaigns,
        "provider": provider,
        "cached": False,
        **({"upstream_error": upstream_error} if upstream_error else {}),
    }


@router.get("/campaign/store-products")
def campaign_store_products(
    store_id: str,
    campaign_account_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    store_key = str(store_id or "").strip()
    if not store_key.isdigit():
        raise HTTPException(status_code=422, detail="Gecerli sayisal store_id zorunlu.")
    base_url, api_key = _campaign_provider_settings(db, user.workspace_uid, campaign_account_id)
    if not _campaign_api_is_sepetler_ai_v1(base_url):
        raise HTTPException(
            status_code=400,
            detail="Magaza urunleri yalnizca Sepetler AI API (/api/ai/v1) ile desteklenir.",
        )
    products = _build_sepetler_store_discounted_products(base_url=base_url, api_key=api_key, store_id=store_key)
    return {
        "store_id": store_key,
        "products": products,
        "count": len(products),
        "provider": {"base_url": base_url, "source": "sepetler_ai_v1"},
    }


@router.post("/campaign/publish")
def campaign_publish_banner(
    body: dict[str, Any],
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Publish campaign banner to upstream Campaign API only (no Meta / Instagram normalize)."""
    campaign_account_id = str(body.get("campaign_account_id") or "").strip() or None
    base_url, api_key = _campaign_provider_settings(db, user.workspace_uid, campaign_account_id)
    store_id = str(body.get("store_id") or "").strip()
    campaign_id = str(body.get("campaign_id") or "").strip()
    if not store_id or not campaign_id:
        raise HTTPException(status_code=422, detail="store_id ve campaign_id zorunlu.")
    image_url = str(body.get("image_url") or "").strip()
    image_urls = [str(x or "").strip() for x in list(body.get("image_urls") or []) if str(x or "").strip()]
    if image_url and image_url not in image_urls:
        image_urls.insert(0, image_url)
    if not image_urls:
        raise HTTPException(status_code=422, detail="Banner gorseli bulunamadi.")

    if _campaign_api_is_sepetler_ai_v1(base_url):
        publish_result = _sepetler_publish_campaign_banner(
            base_url=base_url,
            api_key=api_key,
            body=body,
            store_id=store_id,
            image_urls=image_urls,
        )
        return {
            "ok": True,
            "banner_size": "1600x704",
            "attempted_paths": ["/banners"],
            "publish_result": publish_result,
            "provider": "sepetler_ai_v1",
        }

    start_date = str(body.get("start_date") or "").strip()
    end_date = str(body.get("end_date") or "").strip()
    payload = {
        "caption": str(body.get("caption") or "").strip(),
        "media": image_urls,
        "banner_size": "1600x704",
        "campaign_dates": {"start_date": start_date, "end_date": end_date},
        "campaign_name": str(body.get("campaign_name") or "").strip(),
        "redirect_url": str(body.get("redirect_url") or "").strip(),
        "pricing": body.get("pricing") if isinstance(body.get("pricing"), dict) else {},
    }
    attempted: list[str] = []
    optional_paths = [
        f"/stores/{urllib.parse.quote(store_id)}/campaigns/{urllib.parse.quote(campaign_id)}/banner",
        f"/stores/{urllib.parse.quote(store_id)}/campaigns/{urllib.parse.quote(campaign_id)}/schedule",
        f"/stores/{urllib.parse.quote(store_id)}/campaigns/{urllib.parse.quote(campaign_id)}",
    ]
    for path in optional_paths:
        attempted.append(path)
        try:
            method = "PUT" if path.endswith(f"/{urllib.parse.quote(campaign_id)}") else "POST"
            _campaign_upstream_request(
                base_url=base_url,
                api_key=api_key,
                method=method,
                path=path,
                payload=payload,
                allow_statuses={404, 405},
            )
            break
        except HTTPException:
            # Optional provider endpoints: publish endpointi zorunlu, digerleri best-effort.
            continue

    publish_path = (
        f"/stores/{urllib.parse.quote(store_id)}/campaigns/{urllib.parse.quote(campaign_id)}/publish"
    )
    attempted.append(publish_path)
    publish_result = _campaign_upstream_request(
        base_url=base_url,
        api_key=api_key,
        method="POST",
        path=publish_path,
        payload=payload,
    )
    return {
        "ok": True,
        "banner_size": "1600x704",
        "attempted_paths": attempted,
        "publish_result": publish_result,
    }


@router.post("/instagram/linked-accounts")
def instagram_linked_accounts(body: InstagramLinkedAccountsRequest):
    """List Facebook Pages (with linked Instagram Business accounts) for the given user token."""
    try:
        accounts = list_instagram_accounts_for_user_token(body.access_token)
        return {"accounts": accounts}
    except Exception as exc:
        _log_api_error(
            endpoint="/social-media/instagram/linked-accounts",
            exc=exc,
            payload={"token_len": len((body.access_token or "").strip())},
        )
        return JSONResponse(status_code=400, content={"error": str(exc)})


@router.post("/instagram/graph-destinations")
def instagram_graph_destinations(body: InstagramLinkedAccountsRequest):
    """Facebook Page + Instagram Business cards (avatars, Page tasks) — matches check.html-style picker."""
    try:
        cards = list_graph_publish_destinations(body.access_token)
        return {"cards": cards}
    except Exception as exc:
        _log_api_error(
            endpoint="/social-media/instagram/graph-destinations",
            exc=exc,
            payload={"token_len": len((body.access_token or "").strip())},
        )
        return JSONResponse(status_code=400, content={"error": str(exc)})


@router.post("/post")
def post(body: PostRequest):
    targets = body.publish_targets
    want_feed = True if targets is None else bool(targets.instagram_post)
    want_story = False if targets is None else bool(targets.instagram_story)
    want_facebook = False if targets is None else bool(targets.facebook_post)

    meta_app_limit_hint_tr = (
        "Meta Graph uygulama istek kotasi (#4): kisa pencerede cok fazla cagri. "
        "Birkac dakika bekleyin; arka arkaya Yayinla tiklamayin. "
        "Konteyner durumu sorgulari da kota tuketir."
    )

    image_urls = [str(x or "").strip() for x in (body.image_urls or []) if str(x or "").strip()]
    if not image_urls and (body.image_url or "").strip():
        image_urls = [body.image_url.strip()]

    carousel_images, reel_videos = partition_publish_media_urls(image_urls)
    fallback_media_url = (image_urls[0] if image_urls else (body.image_url or "").strip()) or ""
    story_urls = ordered_publish_urls_for_stories(image_urls, carousel_images, reel_videos)

    preflight_candidates = collect_image_urls_for_publish_preflight(
        image_urls,
        want_feed=want_feed,
        want_story=want_story,
        want_facebook=want_facebook,
    )
    pf_err = preflight_publish_image_urls_for_graph(*preflight_candidates)
    if pf_err:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "error": pf_err,
                "results": {},
                "errors": {
                    "preflight": pf_err,
                },
            },
        )

    tok = (body.instagram_access_token or "").strip()
    explicit_ig = (body.instagram_user_id or "").strip()
    effective_ig_user_id = explicit_ig
    ig_resolve_rate_limited = False
    if (want_feed or want_story) and tok and not effective_ig_user_id:
        try:
            effective_ig_user_id = resolve_instagram_user_id_from_access_token(tok)
        except RuntimeError as exc:
            if is_meta_application_request_limit_error(str(exc)):
                ig_resolve_rate_limited = True
            effective_ig_user_id = ""

    explicit_fb = (body.facebook_page_id or "").strip()
    effective_fb_page_id = explicit_fb
    fb_resolve_rate_limited = False
    if want_facebook and tok and not effective_fb_page_id:
        try:
            effective_fb_page_id = resolve_single_facebook_page_id_if_obvious(tok)
        except RuntimeError as exc:
            if is_meta_application_request_limit_error(str(exc)):
                fb_resolve_rate_limited = True
            effective_fb_page_id = ""

    results: dict[str, object] = {}
    errors: dict[str, str] = {}
    latest_token: str | None = None
    latest_token_exp: int | None = None

    def _capture_token(payload: dict[str, object]) -> None:
        nonlocal latest_token, latest_token_exp
        tok = payload.get("instagram_access_token")
        if isinstance(tok, str) and tok.strip():
            latest_token = tok
        exp = payload.get("token_expires_in_seconds")
        if isinstance(exp, int):
            latest_token_exp = exp

    if want_feed:
        if not (body.caption or "").strip():
            errors["instagram_post"] = "Caption is required for feed post."
        elif ig_resolve_rate_limited:
            errors["instagram_post"] = meta_app_limit_hint_tr
        elif not effective_ig_user_id:
            errors["instagram_post"] = (
                "Instagram Business hesap kimligi yok veya token ile otomatik cozulemedi; "
                "bagli IG sayfasi secin veya hesabi Graph listesiyle yeniden ekleyin."
            )
        else:
            try:
                if len(reel_videos) > 1:
                    raise RuntimeError(
                        "Birden fazla video tek feed isteginde birlestirilemez; yalnizca bir video secin."
                    )
                if len(reel_videos) == 1:
                    feed_result = post_to_instagram(
                        image_url=reel_videos[-1],
                        caption=body.caption,
                        instagram_access_token=body.instagram_access_token,
                        instagram_user_id=effective_ig_user_id,
                    )
                elif len(carousel_images) > 1:
                    feed_result = post_carousel_to_instagram(
                        image_urls=carousel_images,
                        caption=body.caption,
                        instagram_access_token=body.instagram_access_token,
                        instagram_user_id=effective_ig_user_id,
                    )
                else:
                    feed_result = post_to_instagram(
                        image_url=carousel_images[0] if carousel_images else fallback_media_url,
                        caption=body.caption,
                        instagram_access_token=body.instagram_access_token,
                        instagram_user_id=effective_ig_user_id,
                    )
                results["instagram_post"] = feed_result
                _capture_token(feed_result)
            except Exception as exc:
                _log_graph_publish_exc(
                    endpoint="/social-media/post#instagram_post",
                    exc=exc,
                    payload={
                        "caption_len": len(body.caption or ""),
                        "image_url_head": ((image_urls[0] if image_urls else body.image_url) or "")[:120],
                        "images_count": len(image_urls),
                        "has_instagram_token": bool(tok),
                        "has_instagram_user_id_resolved": bool(effective_ig_user_id),
                    },
                )
                errors["instagram_post"] = str(exc)

    if want_story:
        story_token = latest_token or body.instagram_access_token
        if ig_resolve_rate_limited:
            errors["instagram_story"] = meta_app_limit_hint_tr
        elif not effective_ig_user_id:
            errors["instagram_story"] = (
                "Instagram Business hesap kimligi yok veya token ile otomatik cozulemedi; "
                "bagli IG hesabi secin veya hesabi Graph listesiyle yeniden ekleyin."
            )
        else:
            if not story_urls:
                errors["instagram_story"] = (
                    "Story icin gecerli medya URL'si kalmadi: tum URL'ler on kontrolden gecti "
                    "veya taninamadi (silinmis dosya, 404, veya gecici CDN). Feed ile ayni "
                    "siralamada yalnizca Instagram'in indirebildigi adresler kullanilir."
                )
            else:
                try:
                    if len(story_urls) > 1:
                        story_result = post_story_batch_to_instagram(
                            image_urls=story_urls,
                            instagram_access_token=story_token,
                            instagram_user_id=effective_ig_user_id,
                        )
                    else:
                        story_result = post_story_to_instagram(
                            image_url=story_urls[0],
                            instagram_access_token=story_token,
                            instagram_user_id=effective_ig_user_id,
                        )
                    results["instagram_story"] = story_result
                    _capture_token(story_result)
                except Exception as exc:
                    _log_graph_publish_exc(
                        endpoint="/social-media/post#instagram_story",
                        exc=exc,
                        payload={
                            "image_url_head": ((story_urls[0] if story_urls else body.image_url) or "")[:120],
                            "images_count": len(story_urls),
                            "has_instagram_token": bool((story_token or "").strip()),
                            "has_instagram_user_id_resolved": bool(effective_ig_user_id),
                        },
                    )
                    errors["instagram_story"] = str(exc)

    if want_facebook:
        fb_token = latest_token or body.instagram_access_token
        if not effective_fb_page_id:
            if fb_resolve_rate_limited:
                errors["facebook_post"] = meta_app_limit_hint_tr
            else:
                errors["facebook_post"] = (
                    "Facebook Sayfa kimligi yok; tek sayfa baglantisi varsa otomatik doldurulur. "
                    "Birden fazla sayfa icin Yayinla ekraninda Facebook kartini secin."
                )
        else:
            try:
                if len(reel_videos) > 1:
                    raise RuntimeError(
                        "Birden fazla video tek Facebook isteginde birlestirilemez; yalnizca bir video secin."
                    )
                if len(reel_videos) == 1:
                    fb_result = post_photo_to_facebook(
                        image_url=reel_videos[-1],
                        caption=body.caption,
                        instagram_access_token=fb_token,
                        facebook_page_id=effective_fb_page_id,
                    )
                elif len(carousel_images) > 1:
                    fb_result = post_multi_photo_to_facebook(
                        image_urls=carousel_images,
                        caption=body.caption,
                        instagram_access_token=fb_token,
                        facebook_page_id=effective_fb_page_id,
                    )
                else:
                    fb_result = post_photo_to_facebook(
                        image_url=carousel_images[0] if carousel_images else fallback_media_url,
                        caption=body.caption,
                        instagram_access_token=fb_token,
                        facebook_page_id=effective_fb_page_id,
                    )
                results["facebook_post"] = fb_result
            except Exception as exc:
                _log_graph_publish_exc(
                    endpoint="/social-media/post#facebook_post",
                    exc=exc,
                    payload={
                        "image_url_head": ((image_urls[0] if image_urls else body.image_url) or "")[:120],
                        "images_count": len(image_urls),
                        "caption_len": len(body.caption or ""),
                        "has_token": bool((fb_token or "").strip()),
                        "facebook_page_id": effective_fb_page_id,
                    },
                )
                errors["facebook_post"] = str(exc)

    if not want_feed and not want_story and not want_facebook:
        return JSONResponse(status_code=400, content={"success": False, "error": "Publish target secilmedi."})

    success = bool(results) and not errors
    response: dict[str, object] = {"success": success, "results": results}
    feed_payload = results.get("instagram_post")
    if isinstance(feed_payload, dict) and feed_payload.get("post_id"):
        response["post_id"] = feed_payload["post_id"]
    story_payload = results.get("instagram_story")
    if isinstance(story_payload, dict) and story_payload.get("story_id"):
        response["story_id"] = story_payload["story_id"]
    if isinstance(story_payload, dict) and story_payload.get("story_ids"):
        response["story_ids"] = story_payload["story_ids"]
    fb_payload = results.get("facebook_post")
    if isinstance(fb_payload, dict) and fb_payload.get("photo_id"):
        response["facebook_photo_id"] = fb_payload["photo_id"]
    if isinstance(fb_payload, dict) and fb_payload.get("video_id"):
        response["facebook_video_id"] = fb_payload["video_id"]
    if isinstance(fb_payload, dict) and fb_payload.get("post_id"):
        response["facebook_post_id"] = fb_payload["post_id"]
    if latest_token:
        response["instagram_access_token"] = latest_token
    if latest_token_exp is not None:
        response["token_expires_in_seconds"] = latest_token_exp
    if errors:
        response["errors"] = errors
        if not results:
            return JSONResponse(status_code=400, content={**response, "success": False})
    return response


@router.post("/image/delete")
async def delete_image_endpoint(body: dict):
    """Delete one or more images from storage (R2 or local /media) by URL.

    Body: ``{"url": "..."}`` or ``{"urls": ["...", "..."]}``.
    Errors are logged but do not cause a 500 — each URL is attempted independently.
    """
    urls: list[str] = []
    single = str(body.get("url") or "").strip()
    if single:
        urls.append(single)
    multi = body.get("urls")
    if isinstance(multi, list):
        for u in multi:
            s = str(u or "").strip()
            if s and s not in urls:
                urls.append(s)

    results: list[dict] = []
    for url in urls:
        try:
            delete_image_from_storage(url)
            results.append({"url": url, "deleted": True})
        except Exception as exc:
            _log_api_error(endpoint="/social-media/image/delete", exc=exc, payload={"url": url})
            results.append({"url": url, "deleted": False, "error": str(exc)})

    return {"results": results}


@router.post("/image/upload")
async def upload_image(
    file: UploadFile = File(...),
    storage_scope: str = Form("default"),
    owner_uid: str | None = Form(None),
):
    try:
        content = await file.read()
        url = upload_image_bytes_to_storage(
            content,
            filename=file.filename or "upload.jpg",
            storage_scope=storage_scope or "default",
            owner_uid=owner_uid,
        )
        return {"filename": file.filename, "url": url}
    except Exception as exc:
        _log_api_error(
            endpoint="/social-media/image/upload",
            exc=exc,
            payload={
                "filename": file.filename,
                "content_type": file.content_type,
                "file_size": len(content) if "content" in locals() and content is not None else 0,
            },
        )
        return JSONResponse(status_code=400, content={"error": str(exc)})


@legacy_router.post("/api/caption/generate")
def legacy_caption_generate(body: CaptionRequest):
    return caption_generate(body)


@legacy_router.post("/api/caption/revize")
def legacy_caption_revize(body: RevizeRequest):
    return caption_revize(body)


@legacy_router.post("/api/flow/generate-images")
def legacy_flow_generate_images(body: ImageGenerateRequest):
    return flow_generate_images(body)


@legacy_router.post("/api/flow/generate-from-reference")
def legacy_flow_generate_from_reference(body: ImageReferenceGenerateRequest):
    return flow_generate_from_reference(body)


@legacy_router.post("/api/flow/revise-image")
def legacy_flow_revise_image(body: ImageReviseRequest):
    return flow_revise_image(body)


@legacy_router.post("/api/flow/session/start")
def legacy_flow_session_start(body: FlowSessionStartRequest):
    return flow_session_start(body)


@legacy_router.post("/api/flow/session/feedback")
def legacy_flow_session_feedback(body: FlowSessionFeedbackRequest):
    return flow_session_feedback(body)


@legacy_router.get("/api/flow/session/{session_id}")
def legacy_flow_session_get(session_id: str):
    return flow_session_get(session_id)


@legacy_router.post("/api/post")
def legacy_post(body: PostRequest):
    return post(body)


@legacy_router.post("/api/image/upload")
async def legacy_upload_image(file: UploadFile = File(...)):
    return await upload_image(file)
