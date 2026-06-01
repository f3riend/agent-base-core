from datetime import datetime, timedelta, timezone

from app.services.content_service import (
    generate_caption,
    generate_images,
    generate_images_from_reference,
    post_to_instagram,
    refine_caption,
    revise_image_with_feedback,
)


def tool_caption_generate(topic: str, tone: str = "profesyonel", gemini_api_key: str | None = None) -> str:
    return generate_caption(topic, tone, gemini_api_key=gemini_api_key)


def tool_caption_refine(caption: str, feedback: str, gemini_api_key: str | None = None) -> str:
    return refine_caption(caption, feedback, gemini_api_key=gemini_api_key)


def tool_image_generate(prompt: str, count: int = 1, gemini_api_key: str | None = None) -> list[dict[str, str]]:
    return generate_images(prompt, count=count, gemini_api_key=gemini_api_key)


def tool_image_upload_storage(prompt: str, gemini_api_key: str | None = None) -> str:
    images = generate_images(prompt, count=1, gemini_api_key=gemini_api_key)
    return images[0]["url"]


def tool_image_generate_from_reference(
    reference_image_url: str,
    prompt: str,
    count: int = 1,
    gemini_api_key: str | None = None,
) -> list[dict[str, str]]:
    return generate_images_from_reference(
        reference_image_url=reference_image_url,
        prompt=prompt,
        count=count,
        gemini_api_key=gemini_api_key,
    )


def tool_image_revise(
    image_url: str,
    feedback: str,
    count: int = 1,
    gemini_api_key: str | None = None,
) -> list[dict[str, str]]:
    return revise_image_with_feedback(
        image_url=image_url,
        feedback=feedback,
        count=count,
        gemini_api_key=gemini_api_key,
    )


def tool_instagram_post(
    image_url: str,
    caption: str,
    instagram_access_token: str | None = None,
    instagram_user_id: str | None = None,
) -> dict:
    return post_to_instagram(
        image_url=image_url,
        caption=caption,
        instagram_access_token=instagram_access_token,
        instagram_user_id=instagram_user_id,
    )


def tool_publish_date_after_days(days: int, *, from_iso: str | None = None) -> str:
    base = datetime.now(timezone.utc)
    if from_iso:
        try:
            base = datetime.fromisoformat(from_iso.replace("Z", "+00:00"))
        except ValueError:
            base = datetime.now(timezone.utc)
    safe_days = max(0, int(days or 0))
    return (base + timedelta(days=safe_days)).date().isoformat()
