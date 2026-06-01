import uuid

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse
from loguru import logger

from app.schemas.agent import AgentCreateRequest, AgentRunRequest, AgentUpdateRequest, ManagerRunRequest
from app.schemas.content import (
    AnalyzeRequest,
    CaptionRequest,
    FlowSessionFeedbackRequest,
    FlowSessionStartRequest,
    HolidayGenerateRequest,
    HolidayGenerateResponse,
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
    generate_images,
    generate_images_from_reference,
    post_multi_photo_to_facebook,
    post_carousel_to_instagram,
    post_story_batch_to_instagram,
    post_photo_to_facebook,
    post_story_to_instagram,
    post_to_instagram,
    refine_caption,
    revise_image_with_feedback,
    upload_image_bytes_to_storage,
)
from app.services.content_intelligence_service import ContentIntelligenceService
from app.services.task_dispatcher import dispatch, use_celery
from app.integrations.instagram_client import list_instagram_accounts_for_user_token, partition_publish_media_urls

_cis = ContentIntelligenceService()

router = APIRouter(prefix="/social-media", tags=["SocialMedia"])
legacy_router = APIRouter(tags=["LegacyApiShim"])
social_media_logger = logger.bind(module="social-media")

manager_service = AgentManagerService()
runtime_service = AgentRuntimeService(manager_service=manager_service)
social_flow = SocialMediaImageFlow()


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


def _sync_generate_images_task(
    prompt: str,
    count: int,
    platform: str,
    reference_image_url: str | None,
    fal_api_key: str | None,
    openai_api_key: str | None,
    use_gpt: bool = False,
    reference_image_urls: list[str] | None = None,
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
        )
    return generate_images(
        prompt,
        count,
        fal_api_key=fal_api_key,
        platform=plat,
        openai_api_key=openai_api_key,
        use_gpt=use_gpt,
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
    extra_instructions: str | None = None,
) -> dict:
    return generate_holiday_content(
        holiday_name=holiday_name,
        date_key=date_key,
        locale=locale,
        openai_api_key=openai_api_key,
        fal_api_key=fal_api_key,
        generate_image=generate_image,
        extra_instructions=extra_instructions,
    )


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
    )
    return {"images": images, "session_id": str(uuid.uuid4())[:10]}


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


@router.post("/caption/generate")
def caption_generate(body: CaptionRequest):
    try:
        dispatched = dispatch(
            "caption_generate",
            _sync_caption_generate_task,
            body.konu,
            body.tone,
            body.openai_api_key or body.gemini_api_key,
        )
        if dispatched.get("queued"):
            return {"queued": True, "task_id": dispatched["task_id"], "status": "pending"}
        return dispatched.get("result") or {}
    except Exception as exc:
        _log_api_error(
            endpoint="/social-media/caption/generate",
            exc=exc,
            payload={"konu_len": len(body.konu or ""), "tone": body.tone, "has_openai_key": bool((body.openai_api_key or body.gemini_api_key or "").strip())},
        )
        return JSONResponse(status_code=400, content={"error": str(exc)})


@router.post("/caption/revize")
def caption_revize(body: RevizeRequest):
    try:
        dispatched = dispatch(
            "caption_revize",
            _sync_caption_revize_task,
            body.mevcut_caption,
            body.revize_talebi,
            body.openai_api_key or body.gemini_api_key,
        )
        if dispatched.get("queued"):
            return {"queued": True, "task_id": dispatched["task_id"], "status": "pending"}
        return dispatched.get("result") or {}
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
            (body.extra_instructions or "").strip() or None,
        )
        if dispatched.get("queued"):
            return {"queued": True, "task_id": dispatched["task_id"], "status": "pending"}
        result = dispatched.get("result") or {}
        return HolidayGenerateResponse(**result)
    except (ValueError, RuntimeError) as exc:
        _log_api_error(endpoint="/social-media/holiday/generate", exc=exc)
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
def flow_generate_images(body: ImageGenerateRequest):
    """Generate images — async (Celery task) when USE_CELERY=true, otherwise sync."""
    try:
        platform = getattr(body, "platform", "feed") or "feed"
        reference_image_url = getattr(body, "reference_image_url", None)

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
        )
        if dispatched.get("queued"):
            return {"queued": True, "task_id": dispatched["task_id"], "status": "pending"}
        images = (dispatched.get("result") or [])
        return {"session_id": str(uuid.uuid4())[:10], "images": images}
    except Exception as exc:
        _log_api_error(
            endpoint="/social-media/flow/generate-images",
            exc=exc,
            payload={"prompt_len": len(body.prompt or ""), "count": body.count, "has_fal_key": bool((body.fal_api_key or body.gemini_api_key or "").strip())},
        )
        return JSONResponse(status_code=400, content={"error": str(exc)})


@router.post("/flow/generate-from-reference")
def flow_generate_from_reference(body: ImageReferenceGenerateRequest):
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
        )
        if dispatched.get("queued"):
            return {"queued": True, "task_id": dispatched["task_id"], "status": "pending"}
        payload = dispatched.get("result") or {}
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
def flow_revise_image(body: ImageReviseRequest):
    """Revise image — async (Celery task) when USE_CELERY=true, otherwise sync."""
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
        return {"session_id": str(uuid.uuid4())[:10], "images": images}
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
    """Poll Celery task status. Returns 404 if USE_CELERY=false."""
    if not use_celery():
        return JSONResponse(
            status_code=404,
            content={"error": "Celery is not enabled (USE_CELERY env var is not set)."},
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
        return JSONResponse(status_code=400, content={"error": str(exc)})


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


@router.post("/post")
def post(body: PostRequest):
    targets = body.publish_targets
    want_feed = True if targets is None else bool(targets.instagram_post)
    want_story = False if targets is None else bool(targets.instagram_story)
    want_facebook = False if targets is None else bool(targets.facebook_post)

    image_urls = [str(x or "").strip() for x in (body.image_urls or []) if str(x or "").strip()]
    if not image_urls and (body.image_url or "").strip():
        image_urls = [body.image_url.strip()]

    carousel_images, reel_videos = partition_publish_media_urls(image_urls)
    fallback_media_url = (image_urls[0] if image_urls else (body.image_url or "").strip()) or ""

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
                        instagram_user_id=body.instagram_user_id,
                    )
                elif len(carousel_images) > 1:
                    feed_result = post_carousel_to_instagram(
                        image_urls=carousel_images,
                        caption=body.caption,
                        instagram_access_token=body.instagram_access_token,
                        instagram_user_id=body.instagram_user_id,
                    )
                else:
                    feed_result = post_to_instagram(
                        image_url=carousel_images[0] if carousel_images else fallback_media_url,
                        caption=body.caption,
                        instagram_access_token=body.instagram_access_token,
                        instagram_user_id=body.instagram_user_id,
                    )
                results["instagram_post"] = feed_result
                _capture_token(feed_result)
            except Exception as exc:
                _log_api_error(
                    endpoint="/social-media/post#instagram_post",
                    exc=exc,
                    payload={
                        "caption_len": len(body.caption or ""),
                        "image_url_head": ((image_urls[0] if image_urls else body.image_url) or "")[:120],
                        "images_count": len(image_urls),
                        "has_instagram_token": bool((body.instagram_access_token or "").strip()),
                        "has_instagram_user_id": bool((body.instagram_user_id or "").strip()),
                    },
                )
                errors["instagram_post"] = str(exc)

    if want_story:
        story_token = latest_token or body.instagram_access_token
        try:
            if len(image_urls) > 1:
                story_result = post_story_batch_to_instagram(
                    image_urls=image_urls,
                    instagram_access_token=story_token,
                    instagram_user_id=body.instagram_user_id,
                )
            else:
                story_result = post_story_to_instagram(
                    image_url=image_urls[0] if image_urls else body.image_url,
                    instagram_access_token=story_token,
                    instagram_user_id=body.instagram_user_id,
                )
            results["instagram_story"] = story_result
            _capture_token(story_result)
        except Exception as exc:
            _log_api_error(
                endpoint="/social-media/post#instagram_story",
                exc=exc,
                payload={
                    "image_url_head": ((image_urls[0] if image_urls else body.image_url) or "")[:120],
                    "images_count": len(image_urls),
                    "has_instagram_token": bool((story_token or "").strip()),
                    "has_instagram_user_id": bool((body.instagram_user_id or "").strip()),
                },
            )
            errors["instagram_story"] = str(exc)

    if want_facebook:
        fb_token = latest_token or body.instagram_access_token
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
                    facebook_page_id=body.facebook_page_id,
                )
            elif len(carousel_images) > 1:
                fb_result = post_multi_photo_to_facebook(
                    image_urls=carousel_images,
                    caption=body.caption,
                    instagram_access_token=fb_token,
                    facebook_page_id=body.facebook_page_id,
                )
            else:
                fb_result = post_photo_to_facebook(
                    image_url=carousel_images[0] if carousel_images else fallback_media_url,
                    caption=body.caption,
                    instagram_access_token=fb_token,
                    facebook_page_id=body.facebook_page_id,
                )
            results["facebook_post"] = fb_result
        except Exception as exc:
            _log_api_error(
                endpoint="/social-media/post#facebook_post",
                exc=exc,
                payload={
                    "image_url_head": ((image_urls[0] if image_urls else body.image_url) or "")[:120],
                    "images_count": len(image_urls),
                    "caption_len": len(body.caption or ""),
                    "has_token": bool((fb_token or "").strip()),
                    "facebook_page_id": body.facebook_page_id,
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
