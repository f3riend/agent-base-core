"""Celery tasks for async image generation and multi-platform publishing.

Task state progress values:
  10  — ContentIntelligenceService analysis complete
  30  — PromptBuilder prompt built
  60  — Image model call complete
  100 — Images stored (R2 or local)

All tasks use JSON-serialisable arguments only (no Pydantic objects).
"""

from __future__ import annotations

import concurrent.futures
import uuid
from typing import Any

from loguru import logger

from app.core.celery_app import celery_app
from app.integrations.instagram_client import collect_image_urls_for_publish_preflight
from app.services.content_service import (
    generate_caption,
    generate_holiday_content,
    generate_social_video,
    generate_images,
    generate_images_from_reference,
    refine_caption,
    revise_image_with_feedback,
    post_to_instagram,
    post_story_to_instagram,
    post_photo_to_facebook,
    post_carousel_to_instagram,
    post_story_batch_to_instagram,
    post_multi_photo_to_facebook,
    preflight_publish_image_urls_for_graph,
)
from app.services.content_intelligence_service import ContentIntelligenceService
from app.services.prompt_builder import PromptBuilder

_logger = logger.bind(module="image-tasks")
_cis = ContentIntelligenceService()
_pb = PromptBuilder()


@celery_app.task(bind=True, max_retries=1, default_retry_delay=5, name="app.tasks.image_tasks.holiday_generate_task")
def holiday_generate_task(
    self,
    holiday_name: str,
    date_key: str,
    locale: str,
    openai_api_key: str | None,
    fal_api_key: str | None,
    generate_image: bool,
    generate_video: bool = False,
    extra_instructions: str | None = None,
) -> dict[str, Any]:
    task_id = self.request.id or "unknown"
    _logger.info("holiday_generate_task start task_id={} holiday={} date={}", task_id, holiday_name, date_key)
    try:
        self.update_state(state="STARTED", meta={"progress": 5, "step": "holiday_prompting"})
        result = generate_holiday_content(
            holiday_name=holiday_name,
            date_key=date_key,
            locale=locale,
            openai_api_key=openai_api_key,
            fal_api_key=fal_api_key,
            generate_image=generate_image,
            generate_video=generate_video,
            extra_instructions=extra_instructions,
        )
        self.update_state(state="STARTED", meta={"progress": 100, "step": "done"})
        _logger.info(
            "holiday_generate_task done task_id={} has_image={} has_video={}",
            task_id,
            bool(result.get("image_url")),
            bool(result.get("video_url")),
        )
        return result
    except Exception as exc:
        _logger.exception("holiday_generate_task failed task_id={}", task_id)
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            raise RuntimeError(f"Holiday generation failed after retries: {exc}") from exc


@celery_app.task(bind=True, max_retries=1, default_retry_delay=10, name="app.tasks.image_tasks.video_generate_task")
def video_generate_task(
    self,
    prompt: str,
    fal_api_key: str | None,
    image_url: str | None = None,
    duration_sec: int = 5,
    generate_audio: bool = True,
) -> dict[str, Any]:
    task_id = self.request.id or "unknown"
    _logger.info("video_generate_task start task_id={}", task_id)
    try:
        self.update_state(state="STARTED", meta={"progress": 10, "step": "video_fal"})
        url = generate_social_video(
            prompt,
            fal_api_key=fal_api_key,
            image_url=image_url,
            duration_sec=duration_sec,
            generate_audio=generate_audio,
        )
        self.update_state(state="STARTED", meta={"progress": 100, "step": "done"})
        _logger.info("video_generate_task done task_id={}", task_id)
        return {"video_url": url}
    except Exception as exc:
        _logger.exception("video_generate_task failed task_id={}", task_id)
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            raise RuntimeError(f"Video generation failed after retries: {exc}") from exc


@celery_app.task(bind=True, max_retries=2, default_retry_delay=5, name="app.tasks.image_tasks.caption_generate_task")
def caption_generate_task(self, konu: str, tone: str, openai_api_key: str | None) -> dict[str, Any]:
    task_id = self.request.id or "unknown"
    _logger.info("caption_generate_task start task_id={}", task_id)
    try:
        self.update_state(state="STARTED", meta={"progress": 10, "step": "caption"})
        caption = generate_caption(konu, tone, openai_api_key=openai_api_key)
        self.update_state(state="STARTED", meta={"progress": 100, "step": "done"})
        return {"session_id": str(uuid.uuid4())[:10], "caption": caption, "konu": konu}
    except Exception as exc:
        _logger.exception("caption_generate_task failed task_id={}", task_id)
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            raise RuntimeError(f"Caption generation failed after retries: {exc}") from exc


@celery_app.task(bind=True, max_retries=2, default_retry_delay=5, name="app.tasks.image_tasks.caption_revize_task")
def caption_revize_task(self, mevcut_caption: str, revize_talebi: str, openai_api_key: str | None) -> dict[str, Any]:
    task_id = self.request.id or "unknown"
    _logger.info("caption_revize_task start task_id={}", task_id)
    try:
        self.update_state(state="STARTED", meta={"progress": 10, "step": "caption_revize"})
        caption = refine_caption(mevcut_caption, revize_talebi, openai_api_key=openai_api_key)
        self.update_state(state="STARTED", meta={"progress": 100, "step": "done"})
        return {"caption": caption}
    except Exception as exc:
        _logger.exception("caption_revize_task failed task_id={}", task_id)
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            raise RuntimeError(f"Caption revision failed after retries: {exc}") from exc


@celery_app.task(bind=True, max_retries=2, default_retry_delay=5, name="app.tasks.image_tasks.generate_from_reference_task")
def generate_from_reference_task(
    self,
    reference_image_url: str,
    prompt: str,
    count: int,
    fal_api_key: str | None,
    openai_api_key: str | None,
    mode: str,
    reference_image_urls: list[str] | None,
    skip_professionalization: bool,
    output_size: str | None = None,
) -> dict[str, Any]:
    task_id = self.request.id or "unknown"
    _logger.info("generate_from_reference_task start task_id={}", task_id)
    try:
        self.update_state(state="STARTED", meta={"progress": 5, "step": "reference_generate"})
        images = generate_images_from_reference(
            reference_image_url=reference_image_url,
            prompt=prompt,
            count=count,
            fal_api_key=fal_api_key,
            openai_api_key=openai_api_key,
            mode=mode or "background",
            reference_image_urls=reference_image_urls,
            skip_professionalization=skip_professionalization,
            output_size=output_size,
        )
        self.update_state(state="STARTED", meta={"progress": 100, "step": "done"})
        return {"images": images, "session_id": str(uuid.uuid4())[:10]}
    except Exception as exc:
        _logger.exception("generate_from_reference_task failed task_id={}", task_id)
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            raise RuntimeError(f"Reference image generation failed after retries: {exc}") from exc


# ---------------------------------------------------------------------------
# Image generation task
# ---------------------------------------------------------------------------


@celery_app.task(bind=True, max_retries=2, default_retry_delay=5, name="app.tasks.image_tasks.generate_images_task")
def generate_images_task(
    self,
    prompt: str,
    count: int,
    platform: str,
    reference_image_url: str | None,
    fal_api_key: str | None,
    openai_api_key: str | None,
    use_gpt: bool = False,
    reference_image_urls: list[str] | None = None,
) -> dict[str, Any]:
    """Async image generation.

    1. ContentIntelligenceService.analyze() — progress 10
    2. PromptBuilder builds prompt — progress 30
    3. generate_images() / generate_images_from_reference() — progress 60-100
    """
    task_id = self.request.id or "unknown"
    _logger.info("generate_images_task start task_id={} prompt_len={}", task_id, len(prompt or ""))

    try:
        # Step 1 — analyse
        self.update_state(state="STARTED", meta={"progress": 5, "step": "analyzing"})
        ctx = _cis.analyze(
            user_prompt=prompt,
            reference_image_url=reference_image_url,
            platform=platform,
            openai_api_key=openai_api_key,
        )
        self.update_state(state="STARTED", meta={"progress": 10, "step": "analysis_done"})

        # Step 2 — build prompts (done inside generate_images, but we track it here)
        self.update_state(state="STARTED", meta={"progress": 30, "step": "prompt_built"})

        # Step 3 — generate
        if reference_image_url:
            images = generate_images_from_reference(
                reference_image_url=reference_image_url,
                prompt=prompt,
                count=count,
                fal_api_key=fal_api_key,
                platform=platform,
                openai_api_key=openai_api_key,
                context=ctx,
                reference_image_urls=reference_image_urls,
            )
        else:
            images = generate_images(
                prompt=prompt,
                count=count,
                fal_api_key=fal_api_key,
                platform=platform,
                openai_api_key=openai_api_key,
                context=ctx,
                use_gpt=use_gpt,
            )

        self.update_state(state="STARTED", meta={"progress": 100, "step": "done"})
        _logger.info("generate_images_task done task_id={} images={}", task_id, len(images))
        return {"images": images, "session_id": task_id[:10]}

    except Exception as exc:
        _logger.exception("generate_images_task failed task_id={}", task_id)
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            raise RuntimeError(f"Image generation failed after retries: {exc}") from exc


# ---------------------------------------------------------------------------
# Image revision task
# ---------------------------------------------------------------------------


@celery_app.task(bind=True, max_retries=2, default_retry_delay=5, name="app.tasks.image_tasks.revise_image_task")
def revise_image_task(
    self,
    image_url: str,
    feedback: str,
    count: int,
    platform: str,
    fal_api_key: str | None,
    openai_api_key: str | None,
    reference_image_urls: list[str] | None = None,
    output_size: str | None = None,
    revision_context: str = "social",
) -> dict[str, Any]:
    """Async image revision.

    1. revise_image_with_feedback() — CIS + PromptBuilder when revision_context is social;
       campaign_banner skips feed-oriented CIS and uses wide-canvas revision prompts.
    """
    task_id = self.request.id or "unknown"
    _logger.info("revise_image_task start task_id={}", task_id)

    try:
        self.update_state(state="STARTED", meta={"progress": 10, "step": "revising"})
        images = revise_image_with_feedback(
            image_url=image_url,
            feedback=feedback,
            count=count,
            fal_api_key=fal_api_key,
            platform=platform,
            openai_api_key=openai_api_key,
            reference_image_urls=reference_image_urls,
            output_size=output_size,
            revision_context=revision_context,  # type: ignore[arg-type]
        )

        self.update_state(state="STARTED", meta={"progress": 100, "step": "done"})
        _logger.info("revise_image_task done task_id={} images={}", task_id, len(images))
        return {"images": images, "session_id": task_id[:10]}

    except FileNotFoundError as exc:
        # Şablon görseli silinmiş — retry yapma, direkt hata dön
        _logger.warning("revise_image_task skipped — reference image not found: {}", exc)
        raise RuntimeError(f"Şablon görseli bulunamadı, lütfen tekrar deneyin: {exc}") from exc

    except Exception as exc:
        _logger.exception("revise_image_task failed task_id={}", task_id)
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            raise RuntimeError(f"Image revision failed after retries: {exc}") from exc


# ---------------------------------------------------------------------------
# Parallel multi-platform publish task
# ---------------------------------------------------------------------------


def _publish_instagram_post(
    image_urls: list[str],
    caption: str,
    instagram_access_token: str | None,
    instagram_user_id: str | None,
) -> dict[str, Any]:
    if len(image_urls) > 1:
        return post_carousel_to_instagram(
            image_urls=image_urls,
            caption=caption,
            instagram_access_token=instagram_access_token,
            instagram_user_id=instagram_user_id,
        )
    return post_to_instagram(
        image_url=image_urls[0],
        caption=caption,
        instagram_access_token=instagram_access_token,
        instagram_user_id=instagram_user_id,
    )


def _publish_instagram_story(
    image_urls: list[str],
    instagram_access_token: str | None,
    instagram_user_id: str | None,
) -> dict[str, Any]:
    if len(image_urls) > 1:
        return post_story_batch_to_instagram(
            image_urls=image_urls,
            instagram_access_token=instagram_access_token,
            instagram_user_id=instagram_user_id,
        )
    return post_story_to_instagram(
        image_url=image_urls[0],
        instagram_access_token=instagram_access_token,
        instagram_user_id=instagram_user_id,
    )


def _publish_facebook_post(
    image_urls: list[str],
    caption: str,
    instagram_access_token: str | None,
    facebook_page_id: str | None,
) -> dict[str, Any]:
    if len(image_urls) > 1:
        return post_multi_photo_to_facebook(
            image_urls=image_urls,
            caption=caption,
            instagram_access_token=instagram_access_token,
            facebook_page_id=facebook_page_id,
        )
    return post_photo_to_facebook(
        image_url=image_urls[0],
        caption=caption,
        instagram_access_token=instagram_access_token,
        facebook_page_id=facebook_page_id,
    )


@celery_app.task(bind=True, max_retries=0, name="app.tasks.image_tasks.publish_content_task")
def publish_content_task(
    self,
    image_urls: list[str],
    caption: str,
    publish_targets: dict[str, bool],
    instagram_access_token: str | None,
    instagram_user_id: str | None,
    facebook_page_id: str | None,
) -> dict[str, Any]:
    """Publish content to multiple platforms in parallel via ThreadPoolExecutor.

    Returns a dict with per-platform success/error entries.
    """
    task_id = self.request.id or "unknown"
    _logger.info("publish_content_task start task_id={} targets={}", task_id, publish_targets)

    want_ig_post = bool(publish_targets.get("instagram_post", True))
    want_ig_story = bool(publish_targets.get("instagram_story", False))
    want_fb = bool(publish_targets.get("facebook_post", False))

    urls = [str(u or "").strip() for u in (image_urls or []) if str(u or "").strip()]
    if not urls:
        raise RuntimeError("publish_content_task: no image_urls provided.")

    pf_urls = collect_image_urls_for_publish_preflight(
        urls,
        want_feed=want_ig_post,
        want_story=want_ig_story,
        want_facebook=want_fb,
    )
    pf_err = preflight_publish_image_urls_for_graph(*pf_urls)
    if pf_err:
        return {"success": False, "results": {}, "errors": {"preflight": pf_err}}

    futures_map: dict[str, concurrent.futures.Future] = {}
    results: dict[str, Any] = {}
    errors: dict[str, str] = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        if want_ig_post:
            futures_map["instagram_post"] = executor.submit(
                _publish_instagram_post, urls, caption, instagram_access_token, instagram_user_id
            )
        if want_ig_story:
            futures_map["instagram_story"] = executor.submit(
                _publish_instagram_story, urls, instagram_access_token, instagram_user_id
            )
        if want_fb:
            futures_map["facebook_post"] = executor.submit(
                _publish_facebook_post, urls, caption, instagram_access_token, facebook_page_id
            )

        for platform_key, future in futures_map.items():
            try:
                results[platform_key] = future.result(timeout=120)
            except Exception as exc:
                _logger.exception("publish_content_task platform={} failed", platform_key)
                errors[platform_key] = str(exc)

    success = bool(results) and not errors
    _logger.info(
        "publish_content_task done task_id={} success={} errors={}",
        task_id,
        success,
        list(errors.keys()),
    )
    return {"success": success, "results": results, "errors": errors}