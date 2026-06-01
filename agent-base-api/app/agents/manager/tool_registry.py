from app.agents.tools.social_media_tools import (
    tool_caption_generate,
    tool_caption_refine,
    tool_image_generate,
    tool_image_generate_from_reference,
    tool_image_revise,
    tool_image_upload_storage,
    tool_instagram_post,
    tool_publish_date_after_days,
)


TOOL_REGISTRY: dict[str, callable] = {
    "caption_generate": tool_caption_generate,
    "caption_refine": tool_caption_refine,
    "image_generate": tool_image_generate,
    "image_generate_from_reference": tool_image_generate_from_reference,
    "image_revise": tool_image_revise,
    "image_upload_storage": tool_image_upload_storage,
    "instagram_post": tool_instagram_post,
    "publish_date_after_days": tool_publish_date_after_days,
}


def get_tool(tool_id: str):
    return TOOL_REGISTRY.get(tool_id)


def get_tools(tool_ids: list[str]) -> list[callable]:
    return [TOOL_REGISTRY[t] for t in tool_ids if t in TOOL_REGISTRY]
