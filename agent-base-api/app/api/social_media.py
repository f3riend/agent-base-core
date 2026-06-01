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
from app.runtime import (
    ApprovalService as RuntimeApprovalService,
    MemoryStore as RuntimeMemoryStore,
    ObservabilityService as RuntimeObservabilityService,
    OperationSemantics,
    Orchestrator as RuntimeOrchestrator,
    OperationStore as RuntimeOperationStore,
    PolicyEngine as RuntimePolicyEngine,
    ToolRegistry as RuntimeToolRegistry,
)
from app.runtime.tools.contracts import ToolContext as RuntimeToolContext, ToolMetadata as RuntimeToolMetadata, ToolResult as RuntimeToolResult

_cis = ContentIntelligenceService()

router = APIRouter(prefix="/social-media", tags=["SocialMedia"])
legacy_router = APIRouter(tags=["LegacyApiShim"])
social_media_logger = logger.bind(module="social-media")

manager_service = AgentManagerService()
runtime_service = AgentRuntimeService(manager_service=manager_service)
social_flow = SocialMediaImageFlow()

runtime_operation_store = RuntimeOperationStore()
runtime_tool_registry = RuntimeToolRegistry()
runtime_policy_engine = RuntimePolicyEngine()
runtime_memory_store = RuntimeMemoryStore()
runtime_approval_service = RuntimeApprovalService()
runtime_observability = RuntimeObservabilityService()
runtime_orchestrator = RuntimeOrchestrator(
    store=runtime_operation_store,
    registry=runtime_tool_registry,
    policy_engine=runtime_policy_engine,
    memory=runtime_memory_store,
    approvals=runtime_approval_service,
    observability=runtime_observability,
)

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
_runtime_date_engine = OperationSemantics()

try:
    from crewai import Agent as CrewAgent, Crew as CrewRunner, LLM as CrewLLM, Task as CrewTask

    _CREWAI_AVAILABLE = True
except ImportError:  # pragma: no cover
    _CREWAI_AVAILABLE = False


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


_RUNTIME_TOOLS_REGISTERED = False


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


async def _runtime_tool_noop(ctx: RuntimeToolContext) -> RuntimeToolResult:
    await asyncio.sleep(0.05)
    tool_name = str((ctx.metadata or {}).get("tool") or "")
    output_map = {
        "analyze_product": {"summary": "Satis ve ciro metrikleri tarandi", "signal": "metric_scan"},
        "generate_strategy": {"summary": "Kampanya strateji taslagi olusturuldu", "signal": "strategy_ready"},
        "generate_caption": {"summary": "Metin varyasyonlari hazirlandi", "signal": "caption_ready"},
        "create_approval": {"summary": "Onay adimi olusturuldu", "signal": "approval_required"},
        "schedule_post": {"summary": "Yayin takvimine eklendi", "signal": "scheduled"},
        "publish_queue": {"summary": "Yayin kuyrugu guncellendi", "signal": "queued"},
        "event_generated": {"summary": "Operasyon kaydi olusturuldu", "signal": "event_logged"},
        "analyze_reviews": {"summary": "Yorumlar duygu ve konu bazli ayrildi", "signal": "review_scan"},
        "detect_complaint_clusters": {"summary": "Sikayet kumeleri cikarildi", "signal": "complaint_cluster"},
        "generate_mitigation_plan": {"summary": "Azaltim plani hazirlandi", "signal": "mitigation_ready"},
        "generate_banner_copy": {"summary": "Banner metin onerileri olusturuldu", "signal": "copy_ready"},
        "generate_banner_visual": {"summary": "Banner gorsel taslaklari olusturuldu", "signal": "visual_ready"},
        "load_previous_campaign": {"summary": "Gecmis kampanya performansi yüklendi", "signal": "history_loaded"},
        "optimize_strategy": {"summary": "Strateji optimizasyonu tamamlandi", "signal": "strategy_optimized"},
        "summarize_operational_insights": {"summary": "Operasyon icgoruleri ozetlendi", "signal": "insight_summary"},
        "check_reviews": {"summary": "Yorum kontrolu tamamlandi", "signal": "review_check"},
    }
    return RuntimeToolResult(status="completed", output=output_map.get(tool_name, {"summary": "Adim tamamlandi", "signal": "completed"}))


def _extract_campaign_date_from_text(message: str) -> str:
    """Single date engine bridge for all runtime creative steps."""
    now = datetime.now(timezone.utc)
    try:
        normalized = _runtime_date_engine.normalize_user_text(str(message or ""))
        parsed = str(_runtime_date_engine._date_from_text(normalized) or "").strip()
        if parsed:
            return parsed
    except Exception:
        pass
    return (now + timedelta(days=3)).replace(hour=12, minute=0, second=0, microsecond=0).isoformat()


def _resolve_target_date(context: dict[str, Any], message: str) -> str:
    sem = dict(context.get("operation_semantics") or {})
    return str(sem.get("scheduled_at") or sem.get("target_date") or context.get("campaign_target_date") or _extract_campaign_date_from_text(message))


def _collect_reference_images(context: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    product = dict(context.get("product_item") or {})
    for url in list(product.get("images") or []):
        u = str(url or "").strip()
        if u:
            refs.append(u)
    for asset in list(context.get("product_assets") or []):
        if isinstance(asset, dict):
            u = str(asset.get("url") or asset.get("imageUrl") or "").strip()
            if u:
                refs.append(u)
    current = str(context.get("generated_asset_url") or "").strip()
    if current:
        refs.append(current)
    out: list[str] = []
    for u in refs:
        if not u or u in out:
            continue
        if u.startswith(("http://", "https://")):
            out.append(u)
    return out[:8]


def _safe_preview_url(url: str, seed: str) -> str:
    val = str(url or "").strip()
    if val.startswith(("http://", "https://")):
        return val
    suffix = re.sub(r"[^a-zA-Z0-9_-]", "", str(seed or "runtime"))[:24] or "runtime"
    return f"https://picsum.photos/seed/{suffix}_preview/960/720"


def _is_explicit_approval(message: str, semantics: dict[str, Any]) -> bool:
    txt = str(message or "").lower()
    intent = str(semantics.get("intent") or "").lower()
    operation_action = str(semantics.get("operation_action") or "").lower()
    return (
        intent == "approve_campaign"
        or operation_action in {"schedule_content", "publish_content"}
        or any(k in txt for k in ("onayla", "approve", "takvime ekle", "yayinla"))
    )


def _infer_brand_tone(product_item: dict[str, Any]) -> str:
    category = str(product_item.get("category") or "").lower()
    if any(k in category for k in ("food", "kitchen", "drink", "coffee")):
        return "samimi, sicak ve istah acici"
    if any(k in category for k in ("electronics", "lighting", "tech")):
        return "guven veren, modern ve net"
    if any(k in category for k in ("fashion", "shoes", "accessories")):
        return "stil odakli, rafine ve premium"
    return "guven veren ve sade premium"


def _campaign_hashtags(product_name: str, category: str, occasion: str) -> list[str]:
    raw = [
        "#AnnelerGunu" if "anneler" in occasion.lower() else "#Kampanya",
        "#InstagramPost",
        f"#{re.sub(r'[^a-zA-Z0-9]', '', product_name)[:20]}",
        f"#{re.sub(r'[^a-zA-Z0-9]', '', category)[:16]}",
        "#HemenKesfet",
    ]
    out = []
    for x in raw:
        if x not in out:
            out.append(x)
    return out[:6]


async def _runtime_tool_generate_strategy(ctx: RuntimeToolContext) -> RuntimeToolResult:
    context = dict(ctx.context or {})
    product = dict(context.get("product_item") or {})
    semantics = dict(context.get("operation_semantics") or {})
    reviews = [str(x.get("comment") or "").strip() for x in list(context.get("product_reviews") or []) if isinstance(x, dict)]
    support_issues = [
        str(x.get("title") or x.get("issueType") or "").strip()
        for x in list(context.get("product_support_tickets") or [])
        if isinstance(x, dict)
    ]
    name = str(product.get("name") or "Urun")
    category = str(product.get("category") or "Genel")
    trend = float(context.get("product_trend_pct") or 0.0)
    occasion = "Anneler Gunu" if "anneler" in str(ctx.message or "").lower() else "Mevsimsel Kampanya"
    target_date = _resolve_target_date(context, str(ctx.message or ""))
    content_type = str(semantics.get("content_type") or "instagram_feed_post")
    post_type_map = {
        "instagram_story": "instagram_story",
        "instagram_reel": "instagram_reel",
        "instagram_feed_post": "instagram_feed",
        "social_banner": "social_banner",
        "web_banner": "web_banner",
        "email_campaign": "email_campaign",
    }
    color_approach = "pastel pembe + sicak krem"
    if content_type in {"instagram_story", "instagram_reel"}:
        color_approach = "yuksek kontrast + dikey odak"
    if content_type in {"social_banner", "web_banner"}:
        color_approach = "net baslik alani + marka renk vurgu"
    cta = "Hemen kesfet" if trend >= 0 else "Sinirli sure icin dene"
    review_signal = reviews[0] if reviews else ""
    support_signal = support_issues[0] if support_issues else ""
    creative_reasoning = {
        "product_title": name,
        "category": category,
        "request": str(ctx.message or ""),
        "review_signal": review_signal,
        "support_signal": support_signal,
        "occasion": occasion,
        "tone": _infer_brand_tone(product),
    }
    campaign = {
        "occasion": occasion,
        "target_date": target_date,
        "target_audience": "Hediye odakli ve duygusal satin alma niyeti yuksek kitle",
        "tone": "duygusal ve samimi",
        "cta": cta,
        "post_type": post_type_map.get(content_type, "instagram_feed"),
        "color_approach": color_approach,
        "visual_template": "product-centered-soft-gradient",
        "brand_tone": _infer_brand_tone(product),
        "product_name": name,
        "category": category,
        "platform": str(semantics.get("platform") or "instagram"),
        "content_type": content_type,
        "creative_reasoning": creative_reasoning,
    }
    return RuntimeToolResult(
        status="completed",
        output={
            "summary": "Urun baglamina gore creative strateji olusturuldu",
            "signal": "strategy_ready",
            "event": "campaign_created",
            "description": f"{name} icin {occasion} creative akisi hazirlandi.",
            "context_updates": {"campaign_plan": campaign, "brand_tone": campaign["brand_tone"], "lifecycle_state": "draft"},
            "card": {
                "title": "Kampanya Karti Hazir",
                "description": f"{occasion} icin ton: {campaign['tone']} • CTA: {cta} • Baglam: urun+yorum+destek",
                "meta": campaign,
            },
            "meta": {"occasion": occasion, "target_date": target_date},
        },
    )


async def _runtime_tool_generate_caption(ctx: RuntimeToolContext) -> RuntimeToolResult:
    context = dict(ctx.context or {})
    product = dict(context.get("product_item") or {})
    plan = dict(context.get("campaign_plan") or {})
    semantics = dict(context.get("operation_semantics") or {})
    reviews = [str(x.get("comment") or "").strip() for x in list(context.get("product_reviews") or []) if isinstance(x, dict)]
    support_titles = [
        str(x.get("title") or x.get("issueType") or "").strip()
        for x in list(context.get("product_support_tickets") or [])
        if isinstance(x, dict)
    ]
    name = str(product.get("name") or plan.get("product_name") or "Urun")
    category = str(product.get("category") or plan.get("category") or "Genel")
    tone = str(plan.get("tone") or "duygusal ve samimi")
    occasion = str(plan.get("occasion") or "kampanya")
    content_type = str(semantics.get("content_type") or plan.get("content_type") or "instagram_feed_post")
    user_focus_line = ""
    if reviews:
        user_focus_line = f"Yorumlarda one cikan nokta: {reviews[0][:96]}"
    elif support_titles:
        user_focus_line = f"Destek kayitlarinda one cikan konu: {support_titles[0][:96]}"
    openai_key = str(context.get("openai_api_key") or "").strip() or None
    reference_urls = _collect_reference_images(context)
    platform_hint = "feed"
    if content_type == "instagram_story":
        platform_hint = "story"
    elif content_type in {"instagram_reel", "email_campaign"}:
        platform_hint = "video"
    creative_brief = (
        f"Urun: {name} ({category}). "
        f"Occasion: {occasion}. "
        f"Kullanici istegi: {str(ctx.message or '').strip()}. "
        f"Ton: {tone}. "
        f"{user_focus_line or 'Yorum ve destek sinyali dengeli.'} "
        f"Platform: {platform_hint}."
    )
    cis_ctx = None
    try:
        cis_ctx = _cis.analyze(
            user_prompt=creative_brief,
            reference_image_url=reference_urls[0] if reference_urls else None,
            platform=platform_hint,
            openai_api_key=openai_key,
        )
    except Exception:
        cis_ctx = None
    caption = await asyncio.to_thread(
        generate_caption,
        konu=creative_brief,
        tone=tone,
        openai_api_key=openai_key,
        context=cis_ctx,
        platform=platform_hint,
        reference_image_url=reference_urls[0] if reference_urls else None,
    )
    hashtags = re.findall(r"(#[A-Za-z0-9_]+)", caption or "")
    if not hashtags:
        hashtags = _campaign_hashtags(name, category, occasion)
    short_desc = f"{occasion} odakli Instagram feed kampanyasi"
    if content_type == "instagram_story":
        short_desc = f"{occasion} odakli Instagram story kampanyasi"
        visual_direction = "Dikey format, kisa metin, hizli CTA vurgu"
    elif content_type == "instagram_reel":
        short_desc = f"{occasion} odakli Instagram reel kampanyasi"
        visual_direction = "Dikey hareketli kompozisyon, ilk 2 saniye dikkat vurgu"
    elif content_type in {"social_banner", "web_banner"}:
        short_desc = f"{occasion} odakli banner kampanyasi"
        visual_direction = "Yatay banner, net baslik alani, urun odakli CTA"
    elif content_type == "email_campaign":
        short_desc = f"{occasion} odakli email kampanyasi"
        visual_direction = "Header odakli email hero gorseli, net teklif alani"
    else:
        visual_direction = "Urun merkeze alinmis, yumusak isik, duygusal ve sicak arka plan"
    if cis_ctx is not None:
        visual_direction = str(cis_ctx.scene_composition or cis_ctx.scene_mood or visual_direction)
    content_pack = {
        "caption": caption,
        "hashtags": hashtags,
        "description": short_desc,
        "visual_direction": visual_direction,
        "tone": tone,
        "content_type": content_type,
        "user_focus_line": user_focus_line,
        "creative_brief": creative_brief,
        "reference_image_urls": reference_urls,
    }
    return RuntimeToolResult(
        status="completed",
        output={
            "summary": "Caption Studio motoru ile olusturuldu",
            "signal": "caption_ready",
            "context_updates": {"content_pack": content_pack, "lifecycle_state": str((ctx.context or {}).get("lifecycle_state") or "draft")},
            "card": {
                "title": "Icerik Paketi Hazir",
                "description": f"{short_desc} • {len(hashtags)} hashtag • Studio pipeline",
                "meta": content_pack,
            },
            "meta": {"hashtags": hashtags},
        },
    )


async def _runtime_tool_generate_image(ctx: RuntimeToolContext) -> RuntimeToolResult:
    context = dict(ctx.context or {})
    plan = dict(context.get("campaign_plan") or {})
    pack = dict(context.get("content_pack") or {})
    product = dict(context.get("product_item") or {})
    semantics = dict(context.get("operation_semantics") or {})
    reviews = [str(x.get("comment") or "").strip() for x in list(context.get("product_reviews") or []) if isinstance(x, dict)]
    support_rows = [
        str(x.get("title") or x.get("issueType") or x.get("detail") or "").strip()
        for x in list(context.get("product_support_tickets") or [])
        if isinstance(x, dict)
    ]
    name = str(product.get("name") or plan.get("product_name") or "Urun")
    visual_direction = str(pack.get("visual_direction") or plan.get("visual_template") or "Premium campaign visual")
    format_hint = str(semantics.get("post_format") or "square_1080")
    user_request = str(ctx.message or "").strip()
    reference_urls = _collect_reference_images(context)
    focus_line = str(pack.get("user_focus_line") or "").strip()
    prompt = str(pack.get("creative_brief") or "").strip()
    if not prompt:
        prompt = (
            f"Urun: {name}. Kategori: {str(product.get('category') or plan.get('category') or 'genel')}. "
            f"Istek: {user_request}. Occasion: {str(plan.get('occasion') or 'sezonsal kampanya')}. "
            f"Yon: {visual_direction}. Focus: {focus_line or 'urun degeri net gorunsun'}. "
            f"Format: {format_hint}. "
            f"Yorum sinyali: {reviews[0] if reviews else 'belirgin sinyal yok'}. "
            f"Destek sinyali: {support_rows[0] if support_rows else 'belirgin sinyal yok'}."
        )
    openai_key = str(context.get("openai_api_key") or "").strip() or None
    product_seed = str(ctx.entity_id or "generic")
    social_media_logger.info(
        "runtime.image.start operation_id={} product_id={} reference_count={} has_openai_key={} content_type={} format_hint={}",
        str(ctx.operation_id or ""),
        str(context.get("product_id") or ""),
        len(reference_urls),
        bool(openai_key),
        str(semantics.get("content_type") or pack.get("content_type") or plan.get("content_type") or ""),
        format_hint,
    )
    try:
        if reference_urls:
            social_media_logger.info(
                "runtime.image.mode operation_id={} mode=reference reference_preview={}",
                str(ctx.operation_id or ""),
                reference_urls[:2],
            )
            images = await asyncio.to_thread(
                generate_images_from_reference,
                reference_image_url=reference_urls[0],
                prompt=prompt,
                count=1,
                openai_api_key=openai_key,
                platform="feed",
                reference_image_urls=reference_urls,
            )
        else:
            social_media_logger.info(
                "runtime.image.mode operation_id={} mode=text_only",
                str(ctx.operation_id or ""),
            )
            images = await asyncio.to_thread(
                generate_images,
                prompt=prompt,
                count=1,
                openai_api_key=openai_key,
                platform="feed",
            )
        image_url = _safe_preview_url(str((images[0] or {}).get("url") or "").strip() if images else "", product_seed)
        if image_url:
            social_media_logger.info(
                "runtime.image.success operation_id={} provider=openai_gpt_image real=true image_url={}",
                str(ctx.operation_id or ""),
                image_url,
            )
            return RuntimeToolResult(
                status="completed",
                output={
                    "provider": "openai_gpt_image",
                    "event": "asset_generated",
                    "description": "Kampanya gorseli olusturuldu ve onizleme hazir.",
                    "context_updates": {
                        "generated_asset_url": image_url,
                        "generated_asset_urls": [image_url],
                        "reference_image_urls": reference_urls,
                        "lifecycle_state": str(context.get("lifecycle_state") or "draft"),
                    },
                    "card": {
                        "title": "Varlik Onizleme Hazir",
                        "description": "Instagram kampanya gorseli uretildi.",
                        "preview_image": image_url,
                    },
                },
                preview=image_url,
                image_url=image_url,
                metadata={"provider": "openai_gpt_image", "real": True, "reference_mode": bool(reference_urls), "reference_count": len(reference_urls)},
            )
    except Exception as exc:
        social_media_logger.warning("runtime.generate_image.failed error={}", str(exc))
    fallback = _safe_preview_url("", f"{product_seed}_runtime")
    social_media_logger.warning(
        "runtime.image.fallback operation_id={} provider=mock_fallback real=false image_url={}",
        str(ctx.operation_id or ""),
        fallback,
    )
    return RuntimeToolResult(
        status="completed",
        output={
            "provider": "mock_fallback",
            "event": "asset_generated",
            "description": "Kampanya gorseli fallback ile olusturuldu.",
            "context_updates": {
                "generated_asset_url": fallback,
                "generated_asset_urls": [fallback],
                "reference_image_urls": reference_urls,
                "lifecycle_state": str(context.get("lifecycle_state") or "draft"),
            },
            "card": {
                "title": "Varlik Onizleme Hazir",
                "description": "Kampanya gorseli onizlemesi olusturuldu.",
                "preview_image": fallback,
            },
        },
        preview=fallback,
        image_url=fallback,
        metadata={"provider": "mock_fallback", "real": False},
    )


async def _runtime_tool_create_approval(ctx: RuntimeToolContext) -> RuntimeToolResult:
    context = dict(ctx.context or {})
    semantics = dict(context.get("operation_semantics") or {})
    plan = dict(context.get("campaign_plan") or {})
    pack = dict(context.get("content_pack") or {})
    product = dict(context.get("product_item") or {})
    db = context.get("db_session")
    workspace_uid = context.get("workspace_uid")
    target_date = _resolve_target_date(context, str(ctx.message or ""))
    try:
        dt = datetime.fromisoformat(target_date.replace("Z", "+00:00"))
    except Exception:
        dt = datetime.now(timezone.utc) + timedelta(days=2)
    platform = str(semantics.get("platform") or plan.get("platform") or "instagram")
    content_type = str(semantics.get("content_type") or pack.get("content_type") or plan.get("content_type") or "instagram_feed_post")
    image_url = _safe_preview_url(str(context.get("generated_asset_url") or "").strip(), str(ctx.entity_id or "draft"))
    draft_payload = {
        "id": "",
        "type": "social_draft",
        "status": "draft",
        "approvalStatus": "pending",
        "approval_state": "pending",
        "platform": platform,
        "contentType": content_type,
        "content_type": content_type,
        "productId": str(context.get("product_id") or ""),
        "productName": str(product.get("name") or plan.get("product_name") or "Urun"),
        "category": str(product.get("category") or plan.get("category") or ""),
        "caption": str(pack.get("caption") or ""),
        "hashtags": list(pack.get("hashtags") or []),
        "visualDirection": str(pack.get("visual_direction") or ""),
        "imageUrl": image_url,
        "imageUrls": [image_url] if image_url else [],
        # Composer Studio compatible draft fields
        "accountId": str(context.get("account_id") or "runtime_ai"),
        "accountName": str(context.get("account_name") or "Timeline AI"),
        "date": dt.date().isoformat(),
        "time": dt.strftime("%H:%M"),
        "prompt": str(pack.get("creative_brief") or ctx.message or ""),
        "publishDate": dt.date().isoformat(),
        "publishTime": dt.strftime("%H:%M"),
        "scheduledAt": dt.isoformat(),
        "scheduled_at": dt.isoformat(),
        "operationId": str(ctx.operation_id or ""),
        "referenceImageUrls": list(pack.get("reference_image_urls") or []),
        "source": "runtime_creative",
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    draft_id = ""
    if isinstance(db, Session) and workspace_uid:
        row = SocialDocument(
            workspace_uid=workspace_uid,
            collection=COMPOSER_DRAFTS_COLLECTION,
            doc_id=f"dr_{uuid.uuid4().hex[:12]}",
            payload=draft_payload,
        )
        db.add(row)
        db.commit()
        draft_id = row.doc_id
        draft_payload["id"] = draft_id
    if str(semantics.get("operation_action") or "") == "save_draft":
        social_media_logger.info(
            "runtime.approval.save_draft operation_id={} draft_id={} target_date={}",
            str(ctx.operation_id or ""),
            draft_id,
            target_date,
        )
        return RuntimeToolResult(
            status="completed",
            output={
                "summary": "Icerik taslak olarak kaydedildi",
                "signal": "draft_saved",
                "event": "draft_saved",
                "description": "Icerik gercek taslak olarak kaydedildi.",
                "context_updates": {"lifecycle_state": "draft", "draft_id": draft_id},
                "card": {
                    "title": "Taslak Hazir",
                    "description": "Taslak kaydedildi. Onay verirsen takvime alinir.",
                    "preview_image": image_url,
                    "meta": {"draft_id": draft_id},
                },
                "meta": {"draft_id": draft_id},
            },
        )
    question = "Takvime eklememi ister misin?"
    social_media_logger.info(
        "runtime.approval.pending operation_id={} draft_id={} target_date={} question={}",
        str(ctx.operation_id or ""),
        draft_id,
        target_date,
        question,
    )
    return RuntimeToolResult(
        status="pending",
        output={
            "summary": "Kampanya onaya alindi",
            "signal": "approval_required",
            "event": "approval_requested",
            "description": question,
            "approval_title": question,
            "pending_action": {
                "id": draft_id or f"pending_{uuid.uuid4().hex[:8]}",
                "title": question,
                "status": "waiting_approval",
            },
            "card": {
                "title": "Onay Bekleniyor",
                "description": f"Kampanya tarihi: {target_date}. {question}",
                "preview_image": image_url,
                "meta": {"target_date": target_date, "draft_id": draft_id},
            },
            "context_updates": {
                "campaign_target_date": target_date,
                "draft_id": draft_id,
                "lifecycle_state": "pending_approval",
            },
            "meta": {"draft_id": draft_id, "target_date": target_date},
        },
    )


async def _runtime_tool_schedule_post(ctx: RuntimeToolContext) -> RuntimeToolResult:
    context = dict(ctx.context or {})
    db = context.get("db_session")
    workspace_uid = context.get("workspace_uid")
    plan = dict(context.get("campaign_plan") or {})
    pack = dict(context.get("content_pack") or {})
    product = dict(context.get("product_item") or {})
    semantics = dict(context.get("operation_semantics") or {})
    asset_url = str(context.get("generated_asset_url") or "").strip()
    target_date_raw = _resolve_target_date(context, str(ctx.message or ""))
    if not target_date_raw:
        target_date_raw = datetime.now(timezone.utc).isoformat()
    try:
        dt = datetime.fromisoformat(target_date_raw.replace("Z", "+00:00"))
    except Exception:
        dt = datetime.now(timezone.utc) + timedelta(days=2)
    platform = str(semantics.get("platform") or plan.get("platform") or "instagram")
    content_type = str(semantics.get("content_type") or pack.get("content_type") or plan.get("content_type") or "instagram_feed_post")
    caption_text = str(pack.get("caption") or "").strip()
    safe_asset_url = _safe_preview_url(asset_url, str(ctx.entity_id or "scheduled")) if asset_url else ""
    draft_id = str(context.get("draft_id") or "").strip()
    missing_fields: list[str] = []
    if not caption_text:
        missing_fields.append("caption")
    if not safe_asset_url:
        missing_fields.append("image")
    if missing_fields:
        if isinstance(db, Session) and workspace_uid and draft_id:
            draft_row = db.scalar(
                select(SocialDocument).where(
                    SocialDocument.workspace_uid == workspace_uid,
                    SocialDocument.collection == COMPOSER_DRAFTS_COLLECTION,
                    SocialDocument.doc_id == draft_id,
                )
            )
            if draft_row is not None:
                payload = dict(draft_row.payload or {})
                payload["status"] = "draft_incomplete"
                payload["approvalStatus"] = "pending"
                payload["validationState"] = "incomplete"
                payload["missingFields"] = missing_fields
                draft_row.payload = payload
                db.commit()
        social_media_logger.warning(
            "runtime.schedule.draft_incomplete operation_id={} missing_fields={} draft_id={}",
            str(ctx.operation_id or ""),
            missing_fields,
            draft_id,
        )
        return RuntimeToolResult(
            status="pending",
            output={
                "summary": "Taslak eksik oldugu icin takvime alinmadi",
                "signal": "draft_incomplete",
                "event": "draft_incomplete",
                "description": f"Eksik alanlar: {', '.join(missing_fields)}. Once taslagi tamamlayalim.",
                "pending_action": {
                    "id": draft_id or f"pending_{uuid.uuid4().hex[:8]}",
                    "title": "Eksik alanlari tamamla (caption + gorsel).",
                    "status": "pending",
                },
                "context_updates": {"lifecycle_state": "draft", "draft_id": draft_id},
                "meta": {"missing_fields": missing_fields, "draft_id": draft_id},
            },
        )
    explicit_approval = _is_explicit_approval(str(ctx.message or ""), semantics)
    if not explicit_approval:
        social_media_logger.warning(
            "runtime.schedule.blocked operation_id={} reason=explicit_approval_required draft_id={}",
            str(ctx.operation_id or ""),
            str(context.get("draft_id") or ""),
        )
        return RuntimeToolResult(
            status="pending",
            output={
                "summary": "Planlama onay bekliyor",
                "signal": "approval_required",
                "event": "approval_requested",
                "description": "Takvime almadan once acik onay gerekiyor.",
                "pending_action": {
                    "id": str(context.get("draft_id") or f"pending_{uuid.uuid4().hex[:8]}"),
                    "title": "Taslagi onayla, sonra takvime alayim.",
                    "status": "waiting_approval",
                },
                "context_updates": {"lifecycle_state": "pending_approval"},
            },
        )
    payload = {
        "id": "",
        "type": "campaign_post",
        "platform": platform,
        "contentType": content_type,
        "content_type": content_type,
        "productId": str(context.get("product_id") or ""),
        "productName": str(product.get("name") or plan.get("product_name") or "Urun"),
        "category": str(product.get("category") or plan.get("category") or ""),
        "caption": str(pack.get("caption") or ""),
        "hashtags": list(pack.get("hashtags") or []),
        "campaignDescription": str(pack.get("description") or ""),
        "visualDirection": str(pack.get("visual_direction") or ""),
        "asset": {"url": safe_asset_url, "kind": "image"},
        "imageUrl": safe_asset_url,
        "imageUrls": [safe_asset_url],
        "operationId": str(ctx.operation_id or ""),
        "operation_id": str(ctx.operation_id or ""),
        "scheduledAt": dt.isoformat(),
        "scheduled_at": dt.isoformat(),
        "scheduledDate": dt.date().isoformat(),
        "scheduledTime": dt.strftime("%H:%M"),
        "status": "scheduled",
        "approvalState": "approved",
        "approval_state": "approved",
        "approvalStatus": "approved",
        "brandTone": str(context.get("brand_tone") or plan.get("brand_tone") or ""),
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    scheduled_id = ""
    if not isinstance(db, Session) or not workspace_uid:
        raise RuntimeError("Scheduled post persistence icin DB baglami gerekli.")
    target_existing_id = str(context.get("target_scheduled_post_id") or context.get("active_campaign_id") or "").strip()
    existing_row = None
    if target_existing_id:
        existing_row = db.scalar(
            select(SocialDocument).where(
                SocialDocument.workspace_uid == workspace_uid,
                SocialDocument.collection == SCHEDULED_POSTS_COLLECTION,
                SocialDocument.doc_id == target_existing_id,
            )
        )
    if existing_row is not None:
        current = dict(existing_row.payload or {})
        current.update(payload)
        existing_row.payload = current
        scheduled_id = existing_row.doc_id
    else:
        row = SocialDocument(
            workspace_uid=workspace_uid,
            collection=SCHEDULED_POSTS_COLLECTION,
            doc_id=f"sp_{uuid.uuid4().hex[:12]}",
            payload=payload,
        )
        db.add(row)
        scheduled_id = row.doc_id
    db.commit()
    payload["id"] = scheduled_id
    if draft_id:
        draft_row = db.scalar(
            select(SocialDocument).where(
                SocialDocument.workspace_uid == workspace_uid,
                SocialDocument.collection == COMPOSER_DRAFTS_COLLECTION,
                SocialDocument.doc_id == draft_id,
            )
        )
        if draft_row is not None:
            db.delete(draft_row)
            db.commit()
    event_name = "scheduled_post_updated" if existing_row is not None else "scheduled_post_created"
    social_media_logger.info(
        "runtime.schedule.success operation_id={} event={} scheduled_post_id={} scheduled_at={} draft_id={}",
        str(ctx.operation_id or ""),
        event_name,
        scheduled_id,
        payload.get("scheduledAt"),
        draft_id,
    )
    summary_text = "Yayin tarihi guncellendi" if existing_row is not None else "Yayin takvimine eklendi"
    description_text = "Mevcut planli icerigin tarihi guncellendi." if existing_row is not None else "Onaylanan kampanya postu takvime eklendi."
    return RuntimeToolResult(
        status="completed",
        output={
            "summary": summary_text,
            "signal": "scheduled",
            "event": event_name,
            "description": description_text,
            "context_updates": {
                "scheduled_post_id": scheduled_id,
                "lifecycle_state": "scheduled",
                "draft_id": "",
            },
            "card": {
                "title": "Takvime Eklendi",
                "description": f"{platform} {content_type} icerigi {payload['scheduledDate']} {payload['scheduledTime']} icin planlandi.",
                "preview_image": safe_asset_url,
                "meta": {"scheduled_post_id": scheduled_id, "scheduled_at": payload["scheduledAt"]},
            },
            "meta": {"scheduled_post_id": scheduled_id},
        },
    )


async def _runtime_tool_publish_queue(ctx: RuntimeToolContext) -> RuntimeToolResult:
    semantics = dict((ctx.context or {}).get("operation_semantics") or {})
    if not bool(semantics.get("publish_now")):
        return RuntimeToolResult(status="completed", output={"summary": "Yayin kuyrugu adimi atlandi", "signal": "queue_skipped"})
    return RuntimeToolResult(
        status="completed",
        output={
            "summary": "Icerik yayin kuyruguna eklendi",
            "signal": "queued",
            "event": "content_queued",
            "description": "Planlanan icerik yayin kuyruguna alindi.",
                "context_updates": {"lifecycle_state": "queued"},
        },
    )


async def _runtime_tool_event_generated(ctx: RuntimeToolContext) -> RuntimeToolResult:
    semantics = dict((ctx.context or {}).get("operation_semantics") or {})
    if bool(semantics.get("publish_now")):
        return RuntimeToolResult(
            status="completed",
            output={
                "summary": "Icerik yayinlandi",
                "signal": "published",
                "event": "content_published",
                "description": "Icerik yayin durumuna gecti.",
                "context_updates": {"lifecycle_state": "published"},
            },
        )
    if str(semantics.get("operation_action") or "") == "save_draft":
        return RuntimeToolResult(
            status="completed",
            output={
                "summary": "Taslak kaydi olusturuldu",
                "signal": "draft_saved",
                "event": "draft_saved",
                "description": "Icerik taslak olarak kaydedildi.",
                "context_updates": {"lifecycle_state": "draft"},
            },
        )
    return RuntimeToolResult(status="completed", output={"summary": "Operasyon kaydi olusturuldu", "signal": "event_logged"})


def _ensure_runtime_tools_registered() -> None:
    global _RUNTIME_TOOLS_REGISTERED
    if _RUNTIME_TOOLS_REGISTERED:
        return
    runtime_tool_registry.register(
        RuntimeToolMetadata(
            tool="generate_image",
            type="image_generation",
            provider="real_or_fallback",
            risk_level="medium",
            requires_approval=False,
            allowed_roles=["admin", "operator"],
        ),
        _runtime_tool_generate_image,
    )
    custom_handlers: dict[str, Callable[[RuntimeToolContext], Awaitable[RuntimeToolResult]]] = {
        "generate_strategy": _runtime_tool_generate_strategy,
        "generate_caption": _runtime_tool_generate_caption,
        "create_approval": _runtime_tool_create_approval,
        "schedule_post": _runtime_tool_schedule_post,
        "publish_queue": _runtime_tool_publish_queue,
        "event_generated": _runtime_tool_event_generated,
    }
    for name in [
        "analyze_product",
        "generate_strategy",
        "generate_caption",
        "create_approval",
        "schedule_post",
        "publish_queue",
        "event_generated",
        "analyze_reviews",
        "detect_complaint_clusters",
        "generate_mitigation_plan",
        "generate_banner_copy",
        "generate_banner_visual",
        "load_previous_campaign",
        "optimize_strategy",
        "summarize_operational_insights",
        "check_reviews",
    ]:
        runtime_tool_registry.register(
            RuntimeToolMetadata(
                tool=name,
                type="runtime_step",
                provider="mock",
                risk_level="low",
                requires_approval=(name in {"create_approval"}),
                allowed_roles=["admin", "operator"],
            ),
            custom_handlers.get(name, _runtime_tool_noop),
        )
    _RUNTIME_TOOLS_REGISTERED = True


def _safe_publish_time(value: str | None) -> str:
    candidate = (value or "").strip()
    if len(candidate) == 5 and candidate[2] == ":" and candidate.replace(":", "").isdigit():
        hh = int(candidate[:2])
        mm = int(candidate[3:])
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return candidate
    return "12:00"


def _match_automation_rule(
    rows: list[SocialDocument],
    event_type: str,
    rule_id: str | None,
) -> SocialDocument | None:
    if rule_id:
        for row in rows:
            if row.doc_id == rule_id:
                return row
        return None
    event_norm = (event_type or "").strip().lower()
    for row in rows:
        payload = row.payload or {}
        if not bool(payload.get("isActive", True)):
            continue
        row_event = str(payload.get("eventType") or "").strip().lower()
        if row_event == event_norm:
            return row
    return None


def _build_automation_planning_prompt(event_type: str, event_payload: dict, rule_payload: dict) -> str:
    tpl = str(rule_payload.get("templatePrompt") or "").strip()
    include_items = rule_payload.get("requiredIncludes")
    includes: list[str] = []
    if isinstance(include_items, list):
        includes = [str(x).strip() for x in include_items if str(x).strip()]
    include_line = ", ".join(includes) if includes else "Yok"
    return (
        f"Event: {event_type}\n"
        f"EventData: {event_payload}\n"
        f"TemplatePrompt: {tpl or 'Yok'}\n"
        f"RequiredIncludes: {include_line}\n"
        "Output must align with Instagram-ready commercial content."
    )


def _crewai_automation_plan(event_type: str, event_payload: dict, rule_payload: dict, openai_api_key: str) -> dict:
    if not _CREWAI_AVAILABLE:
        return {}
    try:
        llm = CrewLLM(model="gpt-4o-mini", api_key=openai_api_key)
        planner = CrewAgent(
            role="Social Automation Planner",
            goal="Produce concise JSON plan for caption and image prompt based on event rule.",
            backstory=(
                "You map business events to social content plans with template, brand tone, "
                "and scheduling constraints."
            ),
            llm=llm,
            verbose=False,
            allow_delegation=False,
        )
        task = CrewTask(
            description=(
                "Given this automation input, produce JSON with keys caption_topic and image_prompt.\n\n"
                f"{_build_automation_planning_prompt(event_type, event_payload, rule_payload)}\n\n"
                "Return JSON only."
            ),
            expected_output='{"caption_topic":"...","image_prompt":"..."}',
            agent=planner,
        )
        raw = str(CrewRunner(agents=[planner], tasks=[task], verbose=False).kickoff()).strip()
        if raw.startswith("```"):
            raw = raw.replace("```json", "").replace("```", "").strip()
        import json

        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception as exc:
        social_media_logger.warning("automation.crewai_plan_failed event={} error={}", event_type, str(exc))
    return {}


def _rule_allowed_tools(rule_payload: dict) -> set[str]:
    raw = rule_payload.get("allowedTools")
    if not isinstance(raw, list):
        return {"caption_generate", "image_generate"}
    out = {str(x).strip() for x in raw if str(x).strip()}
    return out or {"caption_generate", "image_generate"}


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


MOCK_STORES: list[dict] = [
    {
        "id": "store_kadikoy",
        "name": "Kadikoy Kahve",
        "city": "Istanbul",
        "category": "Cafe",
        "status": "active",
        "aiInsightCount": 12,
    },
    {
        "id": "store_besiktas",
        "name": "Besiktas Atelier",
        "city": "Istanbul",
        "category": "Fashion",
        "status": "active",
        "aiInsightCount": 9,
    },
    {
        "id": "store_ankara",
        "name": "Ankara Home Plus",
        "city": "Ankara",
        "category": "Home",
        "status": "draft",
        "aiInsightCount": 7,
    },
]

MOCK_PRODUCTS: list[dict] = [
    {"id": "prd_001", "storeId": "store_kadikoy", "name": "Cold Brew Blend", "category": "Coffee", "status": "active", "price": 129.0, "sales": 482, "trendPct": 14.2, "aiBadges": ["ai_insight", "trending"]},
    {"id": "prd_002", "storeId": "store_kadikoy", "name": "Single Origin Ethiopia", "category": "Coffee", "status": "active", "price": 149.0, "sales": 365, "trendPct": 8.4, "aiBadges": ["ai_insight"]},
    {"id": "prd_003", "storeId": "store_kadikoy", "name": "Hazelnut Latte Syrup", "category": "Syrup", "status": "active", "price": 89.0, "sales": 210, "trendPct": -6.1, "aiBadges": ["risk", "sales_drop"]},
    {"id": "prd_004", "storeId": "store_besiktas", "name": "Urban Overshirt", "category": "Outerwear", "status": "active", "price": 799.0, "sales": 141, "trendPct": 11.7, "aiBadges": ["trending"]},
    {"id": "prd_005", "storeId": "store_besiktas", "name": "Linen Weekend Shirt", "category": "Shirt", "status": "active", "price": 629.0, "sales": 188, "trendPct": 5.0, "aiBadges": ["ai_insight"]},
    {"id": "prd_006", "storeId": "store_besiktas", "name": "Minimal Sneaker", "category": "Shoes", "status": "paused", "price": 1199.0, "sales": 96, "trendPct": -3.2, "aiBadges": ["risk"]},
    {"id": "prd_007", "storeId": "store_ankara", "name": "Smart Desk Lamp", "category": "Lighting", "status": "active", "price": 459.0, "sales": 132, "trendPct": 6.4, "aiBadges": ["ai_insight"]},
    {"id": "prd_008", "storeId": "store_ankara", "name": "Ergonomic Cushion", "category": "Home", "status": "draft", "price": 229.0, "sales": 47, "trendPct": 2.1, "aiBadges": []},
    {"id": "prd_009", "storeId": "store_ankara", "name": "Ceramic Serving Set", "category": "Kitchen", "status": "active", "price": 699.0, "sales": 84, "trendPct": -9.4, "aiBadges": ["sales_drop"]},
    {"id": "prd_010", "storeId": "store_kadikoy", "name": "Reusable Thermo Cup", "category": "Accessories", "status": "active", "price": 249.0, "sales": 275, "trendPct": 9.8, "aiBadges": ["trending"]},
]

MOCK_PRODUCT_DETAILS: dict[str, dict] = {
    "prd_001": {
        "overview": {"sales": 482, "revenue": 62178, "rating": 4.7, "returnRate": 2.3, "trend": [62, 68, 71, 74, 76, 80, 84]},
        "images": [
            "https://picsum.photos/seed/prd_001_main/800/800",
            "https://picsum.photos/seed/prd_001_alt/800/800",
        ],
        "insights": [
            {"type": "warning", "text": "Kargo kaynakli gecikmeler son 10 gunde %18 artti."},
            {"type": "recommendation", "text": "Hafta sonu bundle kampanyalari ile ortalama sepet artiyor."},
            {"type": "analytics", "text": "Aksam 18:00-21:00 saatleri en yuksek siparis zamani."},
        ],
        "reviews": [
            {"id": "r1", "author": "Ayse", "rating": 4, "comment": "Tat guzel ama teslimat gec geldi."},
            {"id": "r2", "author": "Mert", "rating": 5, "comment": "Soguk demleme icin cok basarili."},
        ],
        "orders": [
            {"id": "o101", "date": "2026-05-05", "status": "delivered", "amount": 129.0},
            {"id": "o102", "date": "2026-05-06", "status": "shipped", "amount": 258.0},
        ],
        "history": [
            {"at": "2026-05-01T10:20:00Z", "event": "Price updated"},
            {"at": "2026-05-03T13:42:00Z", "event": "Campaign activated"},
        ],
    }
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


def _crewai_chat_interpretation(message: str, openai_api_key: str) -> dict:
    if not _CREWAI_AVAILABLE:
        return _fallback_chat_interpretation(message)
    fallback = _fallback_chat_interpretation(message)
    try:
        llm = CrewLLM(model="gpt-4o-mini", api_key=openai_api_key)
        planner = CrewAgent(
            role="Automation Chat Planner",
            goal="Convert natural-language scheduling request into structured social automation JSON.",
            backstory=(
                "You are a social automation parser. You extract timing, content focus, platform intent, "
                "and minimal publish strategy from Turkish chat commands."
            ),
            llm=llm,
            verbose=False,
            allow_delegation=False,
        )
        task = CrewTask(
            description=(
                "Parse this user command and return strict JSON only with keys:\n"
                "event_type, delay_days, publish_time, caption_topic, image_prompt, account_name, "
                "account_id, instagram_post, instagram_story, facebook_post, approval_required.\n\n"
                f"User command: {message}\n\n"
                "Rules: publish_time HH:MM, delay_days integer [0..365], Turkish-friendly output."
            ),
            expected_output=(
                '{"event_type":"chat_prompt","delay_days":3,"publish_time":"12:00","caption_topic":"...","image_prompt":"...",'
                '"account_name":"","account_id":"","instagram_post":true,"instagram_story":false,"facebook_post":false,"approval_required":true}'
            ),
            agent=planner,
        )
        raw = str(CrewRunner(agents=[planner], tasks=[task], verbose=False).kickoff()).strip()
        if raw.startswith("```"):
            raw = raw.replace("```json", "").replace("```", "").strip()
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return fallback
        out = dict(fallback)
        out.update(parsed)
        out["delay_days"] = _clamp_delay_days(out.get("delay_days"), default=fallback["delay_days"])
        out["publish_time"] = _safe_publish_time(str(out.get("publish_time") or fallback["publish_time"]))
        out["caption_topic"] = str(out.get("caption_topic") or fallback["caption_topic"]).strip()
        out["image_prompt"] = str(out.get("image_prompt") or fallback["image_prompt"]).strip()
        out["event_type"] = str(out.get("event_type") or "chat_prompt").strip() or "chat_prompt"
        return out
    except Exception as exc:
        social_media_logger.warning("automation.chat_interpretation_failed error={}", str(exc))
        return fallback


_MOCK_AI_MEMORY: dict[str, dict] = {}
_MOCK_OPERATION_TASKS: dict[str, dict] = {}
_MOCK_ENTITY_HISTORY: dict[str, list[dict]] = {}


def _mock_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mock_intent_from_message(message: str) -> str:
    txt = (message or "").lower()
    if "onayla" in txt or "approve" in txt:
        return "approve_campaign"
    if "optimiz" in txt:
        return "optimize_campaign"
    if "kampanya" in txt:
        return "create_campaign"
    if "yorum" in txt:
        return "analyze_reviews"
    if "banner" in txt:
        return "generate_banner"
    return "general_analysis"


def _mock_tools_for_intent(intent: str) -> list[str]:
    mapping = {
        "create_campaign": [
            "analyze_product",
            "check_reviews",
            "generate_strategy",
            "generate_caption",
            "generate_image",
            "create_approval",
        ],
        "approve_campaign": [
            "schedule_post",
            "publish_queue",
            "event_generated",
        ],
        "analyze_reviews": [
            "analyze_reviews",
            "detect_complaint_clusters",
            "generate_mitigation_plan",
        ],
        "generate_banner": [
            "analyze_product",
            "generate_banner_copy",
            "generate_banner_visual",
        ],
        "optimize_campaign": [
            "load_previous_campaign",
            "optimize_strategy",
            "generate_caption",
        ],
        "general_analysis": [
            "analyze_product",
            "summarize_operational_insights",
        ],
    }
    return list(mapping.get(intent) or mapping["general_analysis"])


def _mock_card(
    kind: str,
    title: str,
    description: str,
    actions: list[dict] | None = None,
    preview_image: str | None = None,
) -> AIOperateCard:
    return AIOperateCard(
        type=kind,
        title=title,
        description=description,
        actions=[{"label": str(a.get("label") or ""), "command": str(a.get("command") or "")} for a in (actions or [])],
        preview_image=preview_image,
    )


def _mock_sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _mock_prepare_conversation(body: AIOperateRequest, user: User) -> tuple[str, dict, dict]:
    conversation_id = (body.conversation_id or "").strip() or f"conv_{uuid.uuid4().hex[:12]}"
    memory_key = f"{user.workspace_uid}:{conversation_id}"
    memory = _MOCK_AI_MEMORY.setdefault(
        memory_key,
        {
            "messages": [],
            "entity_memory": {},
            "last_campaign": None,
            "last_insights": [],
            "events": [],
            "pending_actions": [],
        },
    )
    context = {
        "product_id": (body.context.product_id or "").strip(),
        "store_id": (body.context.store_id or "").strip(),
        "order_id": (body.context.order_id or "").strip(),
    }
    for key, value in context.items():
        if value:
            memory["entity_memory"][key] = value
    if body.history:
        memory["messages"] = [x.model_dump(mode="python") for x in body.history][-40:]
    memory["messages"].append({"role": "user", "content": body.message})
    memory["messages"] = memory["messages"][-60:]
    return conversation_id, memory, context


def _mock_cards_and_pending(intent: str, context: dict, memory: dict) -> tuple[list[AIOperateCard], list[AIOperatePendingAction], list[AIOperateEvent], str]:
    cards: list[AIOperateCard] = []
    pending_actions: list[AIOperatePendingAction] = []
    extra_events: list[AIOperateEvent] = []
    assistant_summary = "Operasyon akisi tamamlandi."
    product_seed = context.get("product_id") or "generic"

    if intent == "create_campaign":
        memory["last_campaign"] = {
            "name": "Weekend Momentum Campaign",
            "goal": "AOV +15%",
            "product_id": context.get("product_id") or "",
            "created_at": _mock_now_iso(),
        }
        cards.append(
            _mock_card(
                "recommendation_card",
                "Kampanya Stratejisi Hazir",
                "Hafta sonu bundle + kargo avantajli teklif kombinasyonu onerildi.",
                actions=[
                    {"label": "Generate Banner", "command": "Bu urun icin banner olustur"},
                    {"label": "Schedule Post", "command": "Bu kampanyayi 3 gun sonrasina planla"},
                ],
            )
        )
        cards.append(
            _mock_card(
                "approval_card",
                "Instagram kampanyasi hazir",
                "Kampanya gorseli ve metni approval bekliyor.",
                actions=[
                    {"label": "Approve", "command": "Kampanyayi onayla"},
                    {"label": "Reject", "command": "Kampanyayi reddet ve revize et"},
                ],
                preview_image=f"https://picsum.photos/seed/{product_seed}_approval/640/420",
            )
        )
        pending_actions.append(
            AIOperatePendingAction(
                id=f"pa_{uuid.uuid4().hex[:10]}",
                title="Approval bekleyen kampanya icerigi",
                status="waiting_approval",
                timestamp=_mock_now_iso(),
            )
        )
        extra_events.append(
            AIOperateEvent(
                type="campaign_generated",
                tool="create_campaign",
                status="completed",
                timestamp=_mock_now_iso(),
                description="Campaign package generated",
            )
        )
        extra_events.append(
            AIOperateEvent(
                type="approval_waiting",
                tool="create_approval",
                status="pending",
                timestamp=_mock_now_iso(),
                description="Approval is waiting",
            )
        )
        assistant_summary = "Kampanya olusturuldu, approval adimina alindi."
    elif intent == "approve_campaign":
        cards.append(
            _mock_card(
                "success_card",
                "Campaign Approved",
                "Kampanya onaylandi ve publish queue'ya aktarildi.",
            )
        )
        extra_events.extend(
            [
                AIOperateEvent(type="approval_accepted", tool="schedule_post", status="completed", timestamp=_mock_now_iso(), description="Approval accepted"),
                AIOperateEvent(type="task_completed", tool="publish_queue", status="completed", timestamp=_mock_now_iso(), description="Queued for publish"),
                AIOperateEvent(type="event_generated", tool="event_generated", status="completed", timestamp=_mock_now_iso(), description="Publish event generated"),
            ]
        )
        memory["pending_actions"] = []
        assistant_summary = "Approval aksiyonu tamamlandi, icerik publish queue'ya eklendi."
    elif intent == "analyze_reviews":
        memory["last_insights"] = [
            "Kargo kaynakli sikayet artisi",
            "Aksam saatlerinde siparis piki",
        ]
        cards.append(
            _mock_card(
                "warning_card",
                "Negatif yorum artisi",
                "Kargo kaynakli sikayetlerde %18 artis tespit edildi.",
                actions=[
                    {"label": "Yorumlari Analiz Et", "command": "Bu urunun yorumlarini derin analiz et"},
                    {"label": "Aksiyon Plani", "command": "Kargo aksiyon plani olustur"},
                ],
            )
        )
        cards.append(
            _mock_card(
                "analytics_card",
                "Review Analytics",
                "Aksam 18:00-21:00 arasinda teslimatla ilgili olumsuz yorumlar artis gosteriyor.",
            )
        )
        pending_actions.append(
            AIOperatePendingAction(
                id=f"pa_{uuid.uuid4().hex[:10]}",
                title="12 negatif yorum analiz bekliyor",
                status="waiting_analysis",
                timestamp=_mock_now_iso(),
            )
        )
        extra_events.append(
            AIOperateEvent(
                type="review_spike_detected",
                tool="analyze_reviews",
                status="warning",
                timestamp=_mock_now_iso(),
                description="Review spike detected",
            )
        )
        assistant_summary = "Yorum analizi tamamlandi, risk alanlari isaretlendi."
    elif intent == "generate_banner":
        cards.append(
            _mock_card(
                "success_card",
                "Banner Hazir",
                "Banner metni ve gorsel yonlendirme taslagi olusturuldu.",
                actions=[
                    {"label": "Schedule Post", "command": "Banner postunu takvime ekle"},
                    {"label": "Create Campaign", "command": "Bu banner uzerinden kampanya olustur"},
                ],
                preview_image=f"https://picsum.photos/seed/{product_seed}_banner/640/420",
            )
        )
        extra_events.append(
            AIOperateEvent(
                type="task_completed",
                tool="generate_banner",
                status="completed",
                timestamp=_mock_now_iso(),
                description="Banner generated",
            )
        )
        assistant_summary = "Banner uretildi ve operasyon icin hazir."
    elif intent == "optimize_campaign":
        last_campaign = memory.get("last_campaign")
        if last_campaign:
            cards.append(
                _mock_card(
                    "recommendation_card",
                    "Kampanya optimize edildi",
                    f"{last_campaign.get('name') or 'Kampanya'} yeniden optimize edildi. Yeni hedef: ROAS +12%.",
                    actions=[{"label": "Schedule Post", "command": "Optimize kampanyayi takvime ekle"}],
                )
            )
            extra_events.append(
                AIOperateEvent(
                    type="campaign_generated",
                    tool="optimize_campaign",
                    status="completed",
                    timestamp=_mock_now_iso(),
                    description="Campaign optimized from memory",
                )
            )
            assistant_summary = "Gecmis kampanya memory'den alindi ve optimize edildi."
        else:
            cards.append(
                _mock_card(
                    "warning_card",
                    "Gecmis kampanya bulunamadi",
                    "Memory'de optimize edilecek kampanya yok. Once yeni kampanya olusturun.",
                    actions=[{"label": "Create Campaign", "command": "Bu urun icin kampanya olustur"}],
                )
            )
            assistant_summary = "Memory'de gecmis kampanya bulunamadi."
    else:
        cards.append(
            _mock_card(
                "text",
                "Genel operasyon analizi",
                "Urun performansi stabil, kampanya + yorum analizi ile buyume firsati var.",
                actions=[
                    {"label": "Analyze Reviews", "command": "Bu urunun yorumlarini analiz et"},
                    {"label": "Create Campaign", "command": "Bu urun icin kampanya olustur"},
                ],
            )
        )
        extra_events.append(
            AIOperateEvent(
                type="task_completed",
                tool="general_analysis",
                status="completed",
                timestamp=_mock_now_iso(),
                description="General analysis completed",
            )
        )

    return cards, pending_actions, extra_events, assistant_summary


def _mock_record_entity_history(context: dict, intent: str, summary: str, cards: list[AIOperateCard]) -> None:
    product_id = str(context.get("product_id") or "").strip()
    if not product_id:
        return
    key = f"product:{product_id}"
    row = {
        "id": f"oph_{uuid.uuid4().hex[:10]}",
        "intent": intent,
        "summary": summary,
        "cards": [x.type for x in cards],
        "timestamp": _mock_now_iso(),
    }
    bucket = _MOCK_ENTITY_HISTORY.setdefault(key, [])
    bucket.insert(0, row)
    _MOCK_ENTITY_HISTORY[key] = bucket[:30]


def _mock_create_task(conversation_id: str, intent: str) -> dict:
    task = {
        "task_id": f"task_{uuid.uuid4().hex[:12]}",
        "conversation_id": conversation_id,
        "intent": intent,
        "status": "running",
        "progress": 0,
        "events": [],
        "result": None,
        "created_at": _mock_now_iso(),
    }
    _MOCK_OPERATION_TASKS[task["task_id"]] = task
    return task


class OperationStore:
    def __init__(self) -> None:
        self.tasks = _MOCK_OPERATION_TASKS
        self.task_events: dict[str, list[dict[str, Any]]] = {}
        self.subscribers: dict[str, list[asyncio.Queue]] = {}
        self.approvals: dict[str, dict[str, Any]] = {}
        self._seq = 0
        self._lock = asyncio.Lock()

    async def create_task(self, conversation_id: str, intent: str, context: dict[str, Any]) -> dict[str, Any]:
        task = {
            "task_id": f"task_{uuid.uuid4().hex[:12]}",
            "conversation_id": conversation_id,
            "intent": intent,
            "status": "running",
            "progress": 0,
            "events": [],
            "result": None,
            "context": dict(context or {}),
            "created_at": _mock_now_iso(),
        }
        async with self._lock:
            self.tasks[task["task_id"]] = task
            self.task_events[task["task_id"]] = []
            self.subscribers.setdefault(task["task_id"], [])
        return task

    async def append_event(self, task_id: str, event: str, data: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            self._seq += 1
            envelope = {
                "seq": self._seq,
                "event": event,
                "data": dict(data or {}),
                "timestamp": _mock_now_iso(),
            }
            bucket = self.task_events.setdefault(task_id, [])
            bucket.append(envelope)
            self.tasks.setdefault(task_id, {}).setdefault("events", []).append(envelope)
            queues = list(self.subscribers.get(task_id) or [])
        for q in queues:
            try:
                q.put_nowait(envelope)
            except asyncio.QueueFull:
                continue
        return envelope

    async def update_task(self, task_id: str, **fields: Any) -> None:
        async with self._lock:
            row = self.tasks.get(task_id)
            if row is None:
                return
            row.update(fields)

    async def finalize_task(self, task_id: str, result: dict[str, Any]) -> None:
        await self.update_task(task_id, status="completed", progress=100, result=result)

    async def subscribe(self, task_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=300)
        async with self._lock:
            self.subscribers.setdefault(task_id, []).append(q)
        return q

    async def unsubscribe(self, task_id: str, queue: asyncio.Queue) -> None:
        async with self._lock:
            rows = self.subscribers.get(task_id) or []
            if queue in rows:
                rows.remove(queue)
            self.subscribers[task_id] = rows

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        async with self._lock:
            row = self.tasks.get(task_id)
            return dict(row) if row is not None else None

    async def get_task_events(self, task_id: str) -> list[dict[str, Any]]:
        async with self._lock:
            return [dict(x) for x in (self.task_events.get(task_id) or [])]

    async def create_approval(self, task_id: str, conversation_id: str, context: dict[str, Any]) -> dict[str, Any]:
        approval = {
            "approval_id": f"apr_{uuid.uuid4().hex[:12]}",
            "task_id": task_id,
            "conversation_id": conversation_id,
            "status": "pending",
            "approved_by": None,
            "approved_at": None,
            "created_at": _mock_now_iso(),
            "context": dict(context or {}),
        }
        async with self._lock:
            self.approvals[approval["approval_id"]] = approval
        return approval

    async def approve_latest(self, context: dict[str, Any], approved_by: str) -> dict[str, Any] | None:
        product_id = str((context or {}).get("product_id") or "").strip()
        async with self._lock:
            vals = list(self.approvals.values())
            for row in reversed(vals):
                ctx = row.get("context") or {}
                if str(ctx.get("product_id") or "").strip() != product_id:
                    continue
                if row.get("status") != "pending":
                    continue
                row["status"] = "approved"
                row["approved_by"] = approved_by
                row["approved_at"] = _mock_now_iso()
                return dict(row)
        return None


class EventPublisher:
    def __init__(self, store: OperationStore, task_id: str) -> None:
        self.store = store
        self.task_id = task_id

    async def emit(self, event: str, data: dict[str, Any]) -> None:
        await self.store.append_event(self.task_id, event, data)


ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class ToolRegistry:
    def __init__(self) -> None:
        self._registry: dict[str, dict[str, Any]] = {}

    def register(self, name: str, config: dict[str, Any], handler: ToolHandler) -> None:
        self._registry[name] = {
            "name": name,
            "config": dict(config or {}),
            "handler": handler,
        }

    def get(self, name: str) -> dict[str, Any] | None:
        return self._registry.get(name)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for key, row in self._registry.items():
            out[key] = {
                "type": str((row.get("config") or {}).get("type") or ""),
                "provider": str((row.get("config") or {}).get("provider") or ""),
            }
        return out


_OPERATION_STORE = OperationStore()
_TOOL_REGISTRY = ToolRegistry()


async def _tool_generate_image(ctx: dict[str, Any]) -> dict[str, Any]:
    db: Session = ctx["db"]
    user: User = ctx["user"]
    prompt = str(ctx.get("prompt") or "").strip() or "Premium campaign visual"
    product_seed = str((ctx.get("context") or {}).get("product_id") or "generic")
    try:
        key = _resolve_workspace_openai_key(db, user.workspace_uid, None)
        images = await asyncio.to_thread(
            generate_images,
            prompt=prompt,
            count=1,
            openai_api_key=key,
            platform="feed",
        )
        image_url = str((images[0] or {}).get("url") or "").strip() if images else ""
        if image_url:
            return {
                "image_url": image_url,
                "preview": image_url,
                "metadata": {"provider": "openai_gpt_image", "real": True},
            }
    except Exception as exc:
        social_media_logger.warning("mock.runtime.generate_image_failed error={}", str(exc))
    fallback = f"https://picsum.photos/seed/{product_seed}_runtime/640/420"
    return {
        "image_url": fallback,
        "preview": fallback,
        "metadata": {"provider": "mock_fallback", "real": False},
    }


async def _tool_noop(ctx: dict[str, Any]) -> dict[str, Any]:
    _ = ctx
    await asyncio.sleep(0.01)
    return {}


def _register_default_tools() -> None:
    if _TOOL_REGISTRY.get("generate_image"):
        return
    _TOOL_REGISTRY.register("generate_image", {"type": "image_generation", "provider": "real_or_fallback"}, _tool_generate_image)
    for tool_name in [
        "analyze_product",
        "check_reviews",
        "generate_strategy",
        "generate_caption",
        "create_approval",
        "schedule_post",
        "publish_queue",
        "event_generated",
        "analyze_reviews",
        "detect_complaint_clusters",
        "generate_mitigation_plan",
        "generate_banner_copy",
        "generate_banner_visual",
        "load_previous_campaign",
        "optimize_strategy",
        "summarize_operational_insights",
    ]:
        _TOOL_REGISTRY.register(tool_name, {"type": "runtime_step", "provider": "mock"}, _tool_noop)


_register_default_tools()


class TaskRuntime:
    def __init__(self, store: OperationStore, tool_registry: ToolRegistry) -> None:
        self.store = store
        self.tool_registry = tool_registry

    async def run(self, task: dict[str, Any], body: AIOperateRequest, user: User, db: Session) -> None:
        task_id = str(task["task_id"])
        conversation_id = str(task["conversation_id"])
        publisher = EventPublisher(self.store, task_id)
        memory_key = f"{user.workspace_uid}:{conversation_id}"
        memory = _MOCK_AI_MEMORY.setdefault(
            memory_key,
            {
                "messages": [],
                "entity_memory": {},
                "last_campaign": None,
                "last_insights": [],
                "events": [],
                "pending_actions": [],
            },
        )
        context = {
            "product_id": (body.context.product_id or "").strip(),
            "store_id": (body.context.store_id or "").strip(),
            "order_id": (body.context.order_id or "").strip(),
        }
        for key, value in context.items():
            if value:
                memory["entity_memory"][key] = value
        if body.history:
            memory["messages"] = [x.model_dump(mode="python") for x in body.history][-40:]
        memory["messages"].append({"role": "user", "content": body.message})
        memory["messages"] = memory["messages"][-60:]
        intent = _mock_intent_from_message(body.message)
        tools = _mock_tools_for_intent(intent)
        total = max(1, len(tools))
        accum_events: list[AIOperateEvent] = []
        accum_states: list[AIOperateToolState] = []
        accum_cards: list[AIOperateCard] = []
        accum_messages: list[AIOperateMessage] = []
        accum_pending: list[AIOperatePendingAction] = []

        thinking_map = {
            "analyze_product": "Analyzing sales trends...",
            "check_reviews": "Checking review clusters...",
            "generate_strategy": "Preparing campaign strategy...",
            "generate_caption": "Generating caption...",
            "generate_image": "Generating image...",
            "create_approval": "Waiting approval...",
            "schedule_post": "Scheduling post...",
            "publish_queue": "Pushing into publish queue...",
            "event_generated": "Generating publish event...",
        }

        await publisher.emit("operation", {"task_id": task_id, "conversation_id": conversation_id, "status": "running", "progress": 0})

        for idx, tool in enumerate(tools):
            think = thinking_map.get(tool, f"Running {tool}...")
            await publisher.emit("thinking", {"message": think, "timestamp": _mock_now_iso()})
            await asyncio.sleep(0.2)
            started = AIOperateEvent(type="tool_started", tool=tool, status="running", timestamp=_mock_now_iso(), description=f"{tool} started")
            accum_events.append(started)
            await publisher.emit("tool_state", {"tool": tool, "status": "running", "timestamp": started.timestamp, "description": started.description})

            entry = self.tool_registry.get(tool)
            result: dict[str, Any] = {}
            if entry is not None:
                handler: ToolHandler = entry["handler"]
                result = await handler(
                    {
                        "task_id": task_id,
                        "conversation_id": conversation_id,
                        "context": context,
                        "intent": intent,
                        "message": body.message,
                        "db": db,
                        "user": user,
                        "prompt": body.message,
                    }
                )

            if tool == "create_approval" and intent == "create_campaign":
                approval = await self.store.create_approval(task_id, conversation_id, context)
                await publisher.emit("approval", approval)
            if intent == "approve_campaign" and tool == "schedule_post":
                approved = await self.store.approve_latest(context, getattr(user, "username", "system"))
                if approved is not None:
                    await publisher.emit("approval", approved)

            if tool == "generate_image" and result.get("image_url"):
                asset = {
                    "type": "generated_asset",
                    "tool": tool,
                    "image_url": result.get("image_url"),
                    "preview": result.get("preview"),
                    "metadata": result.get("metadata") or {},
                    "timestamp": _mock_now_iso(),
                }
                await publisher.emit("generated_asset", asset)
                key = f"product:{str(context.get('product_id') or '').strip()}"
                if key != "product:":
                    bucket = _MOCK_ENTITY_HISTORY.setdefault(key, [])
                    bucket.insert(0, {"id": f"asset_{uuid.uuid4().hex[:10]}", "intent": intent, "summary": "Generated asset", "asset": asset, "timestamp": _mock_now_iso()})
                    _MOCK_ENTITY_HISTORY[key] = bucket[:60]

            done_status = "pending" if tool == "create_approval" and intent == "create_campaign" else "completed"
            state_item = AIOperateToolState(tool=tool, status=done_status, timestamp=_mock_now_iso(), description=f"{tool} {done_status}")
            accum_states.append(state_item)
            await publisher.emit("tool_state", state_item.model_dump(mode="python"))
            if done_status == "completed":
                done_evt = AIOperateEvent(type="tool_completed", tool=tool, status="completed", timestamp=_mock_now_iso(), description=f"{tool} completed")
                accum_events.append(done_evt)
                await publisher.emit("event", done_evt.model_dump(mode="python"))
            progress = int(((idx + 1) / total) * 70)
            await self.store.update_task(task_id, progress=progress)
            await publisher.emit("operation", {"task_id": task_id, "conversation_id": conversation_id, "status": "running", "progress": progress})

        cards, pending_actions, extra_events, assistant_summary = _mock_cards_and_pending(intent, context, memory)
        for evt in extra_events:
            accum_events.append(evt)
            await publisher.emit("event", evt.model_dump(mode="python"))
            await asyncio.sleep(0.05)
        for card in cards:
            accum_cards.append(card)
            await publisher.emit("card", card.model_dump(mode="python"))
            await asyncio.sleep(0.05)
        for p in pending_actions:
            accum_pending.append(p)
            await publisher.emit("pending_action", p.model_dump(mode="python"))
            await asyncio.sleep(0.04)

        msg_intent = AIOperateMessage(role="assistant", content=f"Intent: {intent}", timestamp=_mock_now_iso())
        msg_summary = AIOperateMessage(role="assistant", content=assistant_summary, timestamp=_mock_now_iso())
        accum_messages.extend([msg_intent, msg_summary])
        await publisher.emit("message", msg_intent.model_dump(mode="python"))
        await publisher.emit("message", msg_summary.model_dump(mode="python"))

        memory["messages"].extend([{"role": x.role, "content": x.content} for x in accum_messages])
        memory["events"] = [x.model_dump(mode="python") for x in accum_events][-120:]
        memory["pending_actions"] = [x.model_dump(mode="python") for x in accum_pending]
        _mock_record_entity_history(context, intent, assistant_summary, accum_cards)

        response = AIOperateResponse(
            conversation_id=conversation_id,
            events=accum_events,
            tool_states=accum_states,
            cards=accum_cards,
            messages=accum_messages,
            pending_actions=accum_pending,
        )
        await self.store.finalize_task(task_id, response.model_dump(mode="python"))
        await publisher.emit("operation", {"task_id": task_id, "conversation_id": conversation_id, "status": "completed", "progress": 100})
        await publisher.emit("done", response.model_dump(mode="python"))


_TASK_RUNTIME = TaskRuntime(_OPERATION_STORE, _TOOL_REGISTRY)


def _mock_operate_response(body: AIOperateRequest, user: User) -> AIOperateResponse:
    conversation_id, memory, context = _mock_prepare_conversation(body, user)
    intent = _mock_intent_from_message(body.message)
    tools = _mock_tools_for_intent(intent)
    now = datetime.now(timezone.utc)

    events: list[AIOperateEvent] = []
    tool_states: list[AIOperateToolState] = []
    for i, tool in enumerate(tools):
        events.append(AIOperateEvent(type="tool_started", tool=tool, status="running", timestamp=(now + timedelta(seconds=i)).isoformat(), description=f"{tool} started"))
        status = "pending" if tool == "create_approval" and intent == "create_campaign" else "completed"
        tool_states.append(AIOperateToolState(tool=tool, status=status, timestamp=(now + timedelta(seconds=i, milliseconds=350)).isoformat(), description=f"{tool} {status}"))
        if status == "completed":
            events.append(AIOperateEvent(type="tool_completed", tool=tool, status="completed", timestamp=(now + timedelta(seconds=i, milliseconds=700)).isoformat(), description=f"{tool} completed"))

    cards, pending_actions, extra_events, assistant_summary = _mock_cards_and_pending(intent, context, memory)
    events.extend(extra_events)
    memory["pending_actions"] = [x.model_dump(mode="python") for x in pending_actions]

    messages = [
        AIOperateMessage(role="assistant", content=f"Intent: {intent}", timestamp=_mock_now_iso()),
        AIOperateMessage(role="assistant", content=assistant_summary, timestamp=_mock_now_iso()),
    ]
    memory["messages"].extend([{"role": x.role, "content": x.content} for x in messages])
    memory["events"] = [x.model_dump(mode="python") for x in events][-100:]
    _mock_record_entity_history(context, intent, assistant_summary, cards)

    return AIOperateResponse(
        conversation_id=conversation_id,
        events=events,
        tool_states=tool_states,
        cards=cards,
        messages=messages,
        pending_actions=pending_actions,
    )


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


@router.post("/automation/events", response_model=AutomationEventResponse)
def automation_event_ingest(
    body: AutomationEventRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Event -> matching automation rule -> generated scheduled post.

    UI can manage rules with `/social-data/collections/automation_rules`.
    This endpoint resolves the best rule, generates caption/image, and creates
    a `scheduled_posts` draft for future publish date.
    """
    try:
        openai_key = _resolve_workspace_openai_key(db, user.workspace_uid, body.openai_api_key)
        rows = db.scalars(
            select(SocialDocument)
            .where(
                SocialDocument.workspace_uid == user.workspace_uid,
                SocialDocument.collection == AUTOMATION_RULES_COLLECTION,
            )
            .order_by(desc(SocialDocument.updated_at))
        ).all()
        rule_row = _match_automation_rule(rows, body.event_type, body.rule_id)
        if rule_row is None:
            return JSONResponse(status_code=404, content={"error": "Event için aktif otomasyon kuralı bulunamadı."})

        rule = dict(rule_row.payload or {})
        rule_delay = int(rule.get("delayDays", 5) or 5)
        delay_days = int(body.override_delay_days) if body.override_delay_days is not None else max(0, rule_delay)
        publish_date = (datetime.now(timezone.utc) + timedelta(days=delay_days)).date().isoformat()
        publish_time = _safe_publish_time(body.override_publish_time or str(rule.get("publishTime") or "12:00"))
        tone = str(rule.get("captionTone") or "profesyonel").strip() or "profesyonel"
        allowed_tools = _rule_allowed_tools(rule)
        plan = _crewai_automation_plan(body.event_type, body.event_payload, rule, openai_key)
        template_prompt = str(rule.get("templatePrompt") or "").strip()
        caption_topic = str(
            plan.get("caption_topic")
            or template_prompt
            or f"{body.event_type} otomasyonu için sosyal medya paylaşımı oluştur"
        ).strip()
        image_prompt = str(
            plan.get("image_prompt")
            or template_prompt
            or f"{body.event_type} etkinliğini anlatan premium sosyal medya görseli"
        ).strip()

        if "caption_generate" in allowed_tools:
            caption = generate_caption(caption_topic, tone, openai_api_key=openai_key)
        else:
            caption = str(rule.get("fallbackCaption") or caption_topic).strip()

        image_url = ""
        if "image_generate" in allowed_tools:
            image_result = generate_images(
                prompt=image_prompt,
                count=1,
                openai_api_key=openai_key,
                platform="feed",
            )
            image_url = str((image_result[0] or {}).get("url") or "").strip() if image_result else ""
        else:
            image_url = str(body.event_payload.get("imageUrl") or rule.get("fallbackImageUrl") or "").strip()
        if not image_url:
            raise RuntimeError("Otomasyon gorsel URL uretmedi. Kuralda image_generate tool acin veya fallback imageUrl verin.")

        publish_targets = rule.get("publishTargets")
        if not isinstance(publish_targets, dict):
            publish_targets = {"instagram_post": True, "instagram_story": False, "facebook_post": False}

        event_id = str(uuid.uuid4())
        now_iso = datetime.now(timezone.utc).isoformat()
        event_doc = {
            "eventType": body.event_type,
            "eventPayload": body.event_payload,
            "ruleId": rule_row.doc_id,
            "triggeredAt": now_iso,
            "createdAt": now_iso,
        }
        db.add(
            SocialDocument(
                workspace_uid=user.workspace_uid,
                collection=AUTOMATION_EVENTS_COLLECTION,
                doc_id=event_id,
                payload=event_doc,
            )
        )

        scheduled_post_id: str | None = None
        if not body.dry_run:
            scheduled_post_id = str(uuid.uuid4())
            post_doc = {
                "accountId": str(rule.get("accountId") or body.event_payload.get("accountId") or "").strip(),
                "accountName": str(rule.get("accountName") or body.event_payload.get("accountName") or "").strip(),
                "date": publish_date,
                "time": publish_time,
                "prompt": image_prompt,
                "caption": caption,
                "imageUrl": image_url,
                "imageUrls": [image_url],
                "publishStatus": "pending",
                "approvalStatus": "approved" if bool(rule.get("autoApprove", False)) else "pending",
                "publishTargets": publish_targets,
                "source": "automation_rule",
                "automationRuleId": rule_row.doc_id,
                "automationEventId": event_id,
                "eventType": body.event_type,
                "agentTask": str(rule.get("agentTask") or "").strip(),
                "allowedTools": sorted(list(allowed_tools)),
                "createdAt": now_iso,
            }
            db.add(
                SocialDocument(
                    workspace_uid=user.workspace_uid,
                    collection=SCHEDULED_POSTS_COLLECTION,
                    doc_id=scheduled_post_id,
                    payload=post_doc,
                )
            )
        db.commit()

        return AutomationEventResponse(
            queued=False,
            event_id=event_id,
            matched_rule_id=rule_row.doc_id,
            scheduled_post_id=scheduled_post_id,
            scheduled_date=publish_date,
            scheduled_time=publish_time,
            caption=caption,
            image_url=image_url,
            publish_targets=publish_targets,
        )
    except Exception as exc:
        _log_api_error(
            endpoint="/social-media/automation/events",
            exc=exc,
            payload={"event_type": body.event_type, "rule_id": body.rule_id, "dry_run": body.dry_run},
        )
        return JSONResponse(status_code=400, content={"error": str(exc)})


@router.post("/automation/chat-trigger", response_model=AutomationChatTriggerResponse)
def automation_chat_trigger(
    body: AutomationChatTriggerRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Natural-language chat command -> scheduled social post.

    Example command:
    "3 gun sonrasina kadikoy kahve ile ilgili post olustur"
    """
    try:
        openai_key = _resolve_workspace_openai_key(db, user.workspace_uid, None)
        interpretation = _crewai_chat_interpretation(body.message, openai_key)

        matched_rule: SocialDocument | None = None
        rule_payload: dict = {}
        if body.rule_id:
            matched_rule = db.scalar(
                select(SocialDocument).where(
                    SocialDocument.workspace_uid == user.workspace_uid,
                    SocialDocument.collection == AUTOMATION_RULES_COLLECTION,
                    SocialDocument.doc_id == body.rule_id,
                )
            )
            if matched_rule is not None:
                rule_payload = dict(matched_rule.payload or {})

        delay_days = _clamp_delay_days(
            interpretation.get("delay_days"),
            default=_clamp_delay_days(rule_payload.get("delayDays"), default=3),
        )
        publish_date = (datetime.now(timezone.utc) + timedelta(days=delay_days)).date().isoformat()
        publish_time = _safe_publish_time(
            str(interpretation.get("publish_time") or rule_payload.get("publishTime") or "12:00")
        )
        caption_topic = str(
            interpretation.get("caption_topic")
            or rule_payload.get("templatePrompt")
            or body.message
        ).strip()
        image_prompt = str(
            interpretation.get("image_prompt")
            or rule_payload.get("templatePrompt")
            or body.message
        ).strip()
        event_type = str(interpretation.get("event_type") or "chat_prompt").strip() or "chat_prompt"

        tone = str(rule_payload.get("captionTone") or "profesyonel").strip() or "profesyonel"
        caption = generate_caption(caption_topic, tone, openai_api_key=openai_key)
        image_result = generate_images(
            prompt=image_prompt,
            count=1,
            openai_api_key=openai_key,
            platform="feed",
        )
        image_url = str((image_result[0] or {}).get("url") or "").strip() if image_result else ""
        if not image_url:
            raise RuntimeError("Chat komutundan gorsel uretilemedi.")

        publish_targets = rule_payload.get("publishTargets")
        if not isinstance(publish_targets, dict):
            publish_targets = {
                "instagram_post": bool(interpretation.get("instagram_post", True)),
                "instagram_story": bool(interpretation.get("instagram_story", False)),
                "facebook_post": bool(interpretation.get("facebook_post", False)),
            }

        event_id = str(uuid.uuid4())
        now_iso = datetime.now(timezone.utc).isoformat()
        event_doc = {
            "eventType": event_type,
            "eventPayload": {"message": body.message, "interpreted": interpretation},
            "ruleId": matched_rule.doc_id if matched_rule is not None else "",
            "triggeredAt": now_iso,
            "createdAt": now_iso,
            "source": "chat",
        }
        db.add(
            SocialDocument(
                workspace_uid=user.workspace_uid,
                collection=AUTOMATION_EVENTS_COLLECTION,
                doc_id=event_id,
                payload=event_doc,
            )
        )

        scheduled_post_id: str | None = None
        if not body.dry_run:
            scheduled_post_id = str(uuid.uuid4())
            post_doc = {
                "accountId": str(
                    rule_payload.get("accountId")
                    or interpretation.get("account_id")
                    or ""
                ).strip(),
                "accountName": str(
                    rule_payload.get("accountName")
                    or interpretation.get("account_name")
                    or ""
                ).strip(),
                "date": publish_date,
                "time": publish_time,
                "prompt": image_prompt,
                "caption": caption,
                "imageUrl": image_url,
                "imageUrls": [image_url],
                "publishStatus": "pending",
                "approvalStatus": (
                    "pending"
                    if bool(interpretation.get("approval_required", True))
                    else "approved"
                ),
                "publishTargets": publish_targets,
                "source": "automation_chat",
                "automationRuleId": matched_rule.doc_id if matched_rule is not None else "",
                "automationEventId": event_id,
                "eventType": event_type,
                "chatMessage": body.message,
                "createdAt": now_iso,
            }
            db.add(
                SocialDocument(
                    workspace_uid=user.workspace_uid,
                    collection=SCHEDULED_POSTS_COLLECTION,
                    doc_id=scheduled_post_id,
                    payload=post_doc,
                )
            )
        db.commit()

        return AutomationChatTriggerResponse(
            event_id=event_id,
            scheduled_post_id=scheduled_post_id,
            scheduled_date=publish_date,
            scheduled_time=publish_time,
            event_type=event_type,
            caption=caption,
            image_url=image_url,
            interpreted=interpretation,
        )
    except Exception as exc:
        _log_api_error(
            endpoint="/social-media/automation/chat-trigger",
            exc=exc,
            payload={"message_len": len(body.message or ""), "rule_id": body.rule_id, "dry_run": body.dry_run},
        )
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


MOCK_STORES_COLLECTION = "mock_stores_runtime"
MOCK_PRODUCTS_COLLECTION = "mock_products_runtime"


def _mock_seed_if_empty(db: Session, workspace_uid: str, collection: str, seeds: list[dict]) -> None:
    exists = db.scalar(
        select(SocialDocument).where(
            SocialDocument.workspace_uid == workspace_uid,
            SocialDocument.collection == collection,
        )
    )
    if exists is not None:
        return
    now_iso = _mock_now_iso()
    for row in seeds:
        doc_id = str(row.get("id") or uuid.uuid4().hex)
        payload = dict(row)
        payload.setdefault("createdAt", now_iso)
        payload.setdefault("updatedAt", now_iso)
        db.add(
            SocialDocument(
                workspace_uid=workspace_uid,
                collection=collection,
                doc_id=doc_id,
                payload=payload,
            )
        )
    db.commit()


def _mock_list_collection(db: Session, workspace_uid: str, collection: str) -> list[dict]:
    rows = (
        db.scalars(
            select(SocialDocument)
            .where(
                SocialDocument.workspace_uid == workspace_uid,
                SocialDocument.collection == collection,
            )
            .order_by(desc(SocialDocument.updated_at))
        )
        .all()
    )
    out: list[dict] = []
    for row in rows:
        payload = dict(row.payload or {})
        payload.setdefault("id", row.doc_id)
        out.append(payload)
    return out


def _mock_list_collection_by_field(
    db: Session,
    workspace_uid: str,
    collection: str,
    field_name: str,
    field_value: str,
) -> list[dict]:
    if not field_value:
        return []
    rows = _mock_list_collection(db, workspace_uid, collection)
    out: list[dict] = []
    for row in rows:
        if str(row.get(field_name) or "").strip() == field_value:
            out.append(row)
    return out


@router.get("/mock/stores")
def mock_stores(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    products = _mock_list_collection(db, user.workspace_uid, "products")
    buckets: dict[str, dict[str, Any]] = {}
    for p in products:
        sid = str(p.get("storeId") or "").strip()
        if not sid:
            continue
        if sid not in buckets:
            buckets[sid] = {
                "id": sid,
                "name": sid,
                "city": "",
                "category": "General",
                "status": "active",
                "aiInsightCount": 0,
            }
    return {"items": list(buckets.values())}


@router.post("/mock/stores")
def mock_store_create(
    body: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _ = body, db, user
    return JSONResponse(status_code=410, content={"error": "Mock store create devre disi. Gercek urunler icin products koleksiyonunu kullanin."})


@router.get("/mock/products")
def mock_products(
    store_id: str | None = None,
    category: str | None = None,
    status: str | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = _mock_list_collection(db, user.workspace_uid, "products")
    if store_id:
        rows = [x for x in rows if str(x.get("storeId") or "") == store_id]
    if category:
        rows = [x for x in rows if str(x.get("category") or "").lower() == category.lower()]
    if status:
        rows = [x for x in rows if str(x.get("status") or "").lower() == status.lower()]
    if q:
        qn = q.lower().strip()
        rows = [x for x in rows if qn in str(x.get("name") or "").lower()]
    return {"items": rows}


@router.post("/mock/products")
def mock_product_create(
    body: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    name = str((body or {}).get("name") or "").strip()
    if not name:
        return JSONResponse(status_code=400, content={"error": "Product name gerekli."})
    product_id = str((body or {}).get("id") or f"prd_{uuid.uuid4().hex[:6]}")
    now_iso = _mock_now_iso()
    images = list((body or {}).get("images") or [])
    payload = {
        "id": product_id,
        "storeId": str((body or {}).get("storeId") or ""),
        "name": name,
        "category": str((body or {}).get("category") or "General"),
        "status": str((body or {}).get("status") or "active"),
        "price": float((body or {}).get("price") or 0),
        "stock": int((body or {}).get("stock") or 0),
        "description": str((body or {}).get("description") or ""),
        "images": [str(x).strip() for x in images if str(x).strip()],
        "sales": int((body or {}).get("sales") or 0),
        "trendPct": float((body or {}).get("trendPct") or 0),
        "aiBadges": list((body or {}).get("aiBadges") or []),
        "createdAt": now_iso,
        "updatedAt": now_iso,
    }
    db.add(
        SocialDocument(
            workspace_uid=user.workspace_uid,
            collection="products",
            doc_id=product_id,
            payload=payload,
        )
    )
    db.commit()
    return payload


@router.get("/mock/products/{product_id}")
def mock_product_detail(
    product_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = db.scalar(
        select(SocialDocument).where(
            SocialDocument.workspace_uid == user.workspace_uid,
            SocialDocument.collection == "products",
            SocialDocument.doc_id == product_id,
        )
    )
    if row is None:
        return JSONResponse(status_code=404, content={"error": "Mock product not found"})
    item = dict(row.payload or {})
    item.setdefault("id", row.doc_id)
    reviews = _mock_list_collection_by_field(db, user.workspace_uid, "product_reviews", "productId", product_id)
    faq = _mock_list_collection_by_field(db, user.workspace_uid, "product_faq", "productId", product_id)
    tickets = _mock_list_collection_by_field(db, user.workspace_uid, "product_support_tickets", "productId", product_id)
    metrics = _mock_list_collection_by_field(db, user.workspace_uid, "product_metrics_daily", "productId", product_id)
    assets = _mock_list_collection_by_field(db, user.workspace_uid, "product_assets", "productId", product_id)
    trend = []
    for m in metrics[-7:]:
        try:
            trend.append(float(m.get("sales") or 0))
        except Exception:
            continue
    rating = 0.0
    if reviews:
        try:
            rating = round(sum(float(r.get("rating") or 0) for r in reviews) / max(1, len(reviews)), 2)
        except Exception:
            rating = 0.0
    revenue_7d = 0.0
    try:
        revenue_7d = round(sum(float(x.get("revenue") or 0) for x in metrics[-7:]), 2)
    except Exception:
        revenue_7d = 0.0
    return_rate = 0.0
    try:
        if metrics:
            return_rate = round(sum(float(x.get("returnRate") or 0) for x in metrics[-7:]) / max(1, len(metrics[-7:])), 2)
    except Exception:
        return_rate = 0.0
    insights = []
    for t in tickets[:3]:
        issue = str(t.get("issueType") or t.get("title") or "Destek kaydi")
        insights.append({"type": "support", "text": issue})
    detail = {
        "overview": {
            "sales": int(sum(int(float(x.get("sales") or 0)) for x in metrics[-7:])) if metrics else int(item.get("sales") or 0),
            "revenue": revenue_7d,
            "rating": rating,
            "returnRate": return_rate,
            "trend": trend,
        },
        "images": list(item.get("images") or []),
        "insights": insights,
        "reviews": reviews,
        "orders": [],
        "history": [
            {"at": str(a.get("createdAt") or a.get("updatedAt") or _mock_now_iso()), "event": str(a.get("kind") or "Asset guncellendi")}
            for a in assets[:8]
        ],
        "faq": faq,
        "tickets": tickets,
        "metrics": metrics,
    }
    return {"item": item, "detail": detail}


@router.get("/mock/products/{product_id}/reviews")
def mock_product_reviews(
    product_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return {"items": _mock_list_collection_by_field(db, user.workspace_uid, "product_reviews", "productId", product_id)}


@router.get("/mock/products/{product_id}/orders")
def mock_product_orders(
    product_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    metrics = _mock_list_collection_by_field(db, user.workspace_uid, "product_metrics_daily", "productId", product_id)
    rows = [
        {
            "id": str(m.get("id") or f"metric_{idx}"),
            "date": str(m.get("date") or ""),
            "status": "recorded",
            "amount": float(m.get("revenue") or 0),
        }
        for idx, m in enumerate(metrics)
    ]
    return {"items": rows}


@router.get("/mock/products/{product_id}/insights")
def mock_product_insights(
    product_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    tickets = _mock_list_collection_by_field(db, user.workspace_uid, "product_support_tickets", "productId", product_id)
    return {
        "items": [
            {"type": "support", "text": str(t.get("issueType") or t.get("title") or "Destek sinyali")}
            for t in tickets[:8]
        ]
    }


@router.post("/mock/ai-operate", response_model=AIOperateResponse)
def mock_ai_operate(
    body: AIOperateRequest,
    user: User = Depends(get_current_user),
):
    try:
        return _mock_operate_response(body, user)
    except Exception as exc:
        _log_api_error(
            endpoint="/social-media/mock/ai-operate",
            exc=exc,
            payload={"message_len": len(body.message or ""), "conversation_id": body.conversation_id},
        )
        return JSONResponse(status_code=400, content={"error": str(exc)})


@router.post("/mock/ai-operate-stream")
async def mock_ai_operate_stream(
    body: AIOperateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        _ensure_runtime_tools_registered()
        workspace_id = str(user.workspace_uid or "").strip()
        conversation_id = (body.conversation_id or "").strip() or f"conv_{uuid.uuid4().hex[:12]}"
        openai_key = ""
        try:
            openai_key = _resolve_workspace_openai_key(db, workspace_id, None)
        except Exception:
            openai_key = ""
        context = {
            "product_id": (body.context.product_id or "").strip(),
            "store_id": (body.context.store_id or "").strip(),
            "order_id": (body.context.order_id or "").strip(),
            "mode": str(getattr(body.context, "mode", "analiz") or "analiz").strip().lower(),
            "openai_api_key": openai_key,
            "workspace_uid": user.workspace_uid,
            "db_session": db,
        }
        product_id = str(context.get("product_id") or "").strip()
        if product_id:
            product_row = db.scalar(
                select(SocialDocument).where(
                    SocialDocument.workspace_uid == user.workspace_uid,
                    SocialDocument.collection == "products",
                    SocialDocument.doc_id == product_id,
                )
            )
            product_item = dict((product_row.payload or {}) if product_row is not None else {})
            product_reviews = _mock_list_collection_by_field(db, user.workspace_uid, "product_reviews", "productId", product_id)
            product_metrics = _mock_list_collection_by_field(db, user.workspace_uid, "product_metrics_daily", "productId", product_id)
            product_support = _mock_list_collection_by_field(db, user.workspace_uid, "product_support_tickets", "productId", product_id)
            product_faq = _mock_list_collection_by_field(db, user.workspace_uid, "product_faq", "productId", product_id)
            product_assets = _mock_list_collection_by_field(db, user.workspace_uid, "product_assets", "productId", product_id)
            sales_7d = 0
            revenue_7d = 0.0
            trend = []
            for row in product_metrics[-7:]:
                try:
                    s = int(float(row.get("sales") or 0))
                    r = float(row.get("revenue") or 0)
                    sales_7d += s
                    revenue_7d += r
                    trend.append(s)
                except Exception:
                    continue
            rating = 0.0
            if product_reviews:
                try:
                    rating = round(sum(float(x.get("rating") or 0) for x in product_reviews) / max(1, len(product_reviews)), 2)
                except Exception:
                    rating = 0.0
            return_rate = 0.0
            if product_metrics:
                try:
                    return_rate = round(sum(float(x.get("returnRate") or 0) for x in product_metrics[-7:]) / max(1, len(product_metrics[-7:])), 2)
                except Exception:
                    return_rate = 0.0
            product_insights = [
                {"type": "support", "text": str(x.get("issueType") or x.get("title") or "Destek sinyali")}
                for x in product_support[:8]
            ]
            context["product_item"] = product_item
            context["product_overview"] = {
                "sales": sales_7d or int(product_item.get("sales") or 0),
                "revenue": revenue_7d,
                "rating": rating,
                "returnRate": return_rate,
                "trend": trend,
            }
            context["product_reviews"] = list(product_reviews or [])
            context["product_insights"] = list(product_insights or [])
            context["product_history"] = [
                {"at": str(x.get("createdAt") or x.get("updatedAt") or _mock_now_iso()), "event": str(x.get("issueType") or x.get("title") or "Destek kaydi")}
                for x in product_support[:20]
            ]
            context["product_images"] = list(product_item.get("images") or [str(x.get("url") or "") for x in product_assets if str(x.get("url") or "").strip()])
            context["product_faq"] = list(product_faq or [])
            context["product_support_tickets"] = list(product_support or [])
            context["product_metrics_daily"] = list(product_metrics or [])
            context["product_assets"] = list(product_assets or [])
            context["brand_tone"] = str(product_item.get("brandTone") or "")
            context["product_trend_pct"] = float(product_item.get("trendPct") or 0.0)
            context["previous_operations"] = runtime_memory_store.get_entity_memory(workspace_id, "product", product_id)
        op = await runtime_operation_store.create_operation(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            entity_type="product",
            entity_id=str(context.get("product_id") or ""),
        )
        operation_id = str(op["operation_id"])
        queue = await runtime_operation_store.subscribe(workspace_id, operation_id)
        runtime_task = asyncio.create_task(
            runtime_orchestrator.run_operation(
                workspace_id=workspace_id,
                operation_id=operation_id,
                conversation_id=conversation_id,
                message=body.message,
                user_id=str(getattr(user, "id", "") or ""),
                user_role="operator",
                context=context,
                history=[{"role": h.role, "content": h.content} for h in (body.history or [])],
            )
        )

        async def stream():
            try:
                while True:
                    envelope = await queue.get()
                    event_name = str(envelope.get("event_type") or "event")
                    payload = dict(envelope.get("payload") or {})
                    if event_name.startswith("operation."):
                        payload = {**payload, "status": event_name.split(".", 1)[1], "operation_id": operation_id}
                        event_name = "operation"
                    yield _mock_sse(event_name, payload)
                    if event_name == "done":
                        break
            finally:
                await runtime_operation_store.unsubscribe(operation_id, queue)
                if not runtime_task.done():
                    await runtime_task

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except Exception as exc:
        _log_api_error(
            endpoint="/social-media/mock/ai-operate-stream",
            exc=exc,
            payload={"message_len": len(body.message or ""), "conversation_id": body.conversation_id},
        )
        return JSONResponse(status_code=400, content={"error": str(exc)})


@router.get("/mock/operations/{task_id}/stream")
async def mock_operation_task_stream(
    task_id: str,
    user: User = Depends(get_current_user),
):
    _ = user
    try:
        workspace_id = str(user.workspace_uid or "").strip()
        task = await runtime_operation_store.get_operation(workspace_id, task_id)
        if task is None:
            return JSONResponse(status_code=404, content={"error": "Mock operation task not found"})
        return StreamingResponse(
            runtime_orchestrator.stream_replay_and_live(workspace_id, task_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except Exception as exc:
        _log_api_error(
            endpoint=f"/social-media/mock/operations/{task_id}/stream",
            exc=exc,
            payload={"task_id": task_id},
        )
        return JSONResponse(status_code=400, content={"error": str(exc)})


@router.get("/mock/operations/{task_id}")
async def mock_operation_task(
    task_id: str,
    user: User = Depends(get_current_user),
):
    workspace_id = str(user.workspace_uid or "").strip()
    task = await runtime_operation_store.get_operation(workspace_id, task_id)
    if task is None:
        return JSONResponse(status_code=404, content={"error": "Mock operation task not found"})
    return {
        **task,
        "tool_registry": runtime_tool_registry.snapshot(),
    }


@router.get("/mock/operation-history")
def mock_operation_history(
    product_id: str | None = None,
    user: User = Depends(get_current_user),
):
    workspace_id = str(user.workspace_uid or "").strip()
    if not product_id:
        return {"items": []}
    rows = runtime_memory_store.get_entity_memory(workspace_id, "product", str(product_id))
    return {"items": rows}


@router.get("/mock/approvals")
async def mock_approvals(
    task_id: str | None = None,
    user: User = Depends(get_current_user),
):
    workspace_id = str(user.workspace_uid or "").strip()
    rows = runtime_approval_service.list(workspace_id, task_id=task_id)
    return {"items": rows}


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
