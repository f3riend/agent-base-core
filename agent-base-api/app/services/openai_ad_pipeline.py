"""OpenAI-only ad pipeline — ported from ``yedek2.py``.

Used by ``content_service`` for raster generation. When *raw_prompt_is_final* is True
(API integration), user/request strings are passed directly to ``gpt-image-2`` without
the internal prompt-refinement chat steps, so PromptBuilder / CIS output stays intact.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
from typing import Any

import requests
from loguru import logger
from openai import OpenAI

from app.integrations.ai_client import (
    generate_image_with_openai_edit,
    generate_image_with_openai_edit_multi,
    generate_image_with_openai_text,
    resolve_openai_key,
)

IMAGE_MODEL = "gpt-image-2"
TEXT_MODEL = "gpt-4o"
AD_IMAGE_SIZE = "1024x1280"
_DOWNLOAD_TIMEOUT_S = 45

_logger = logger.bind(module="openai-ad-pipeline")


def _download_url_binary(url: str) -> tuple[bytes, str]:
    url = (url or "").strip()
    if not url:
        raise ValueError("Image URL is empty.")
    resp = requests.get(url, timeout=_DOWNLOAD_TIMEOUT_S, stream=True)
    try:
        if resp.status_code >= 400:
            raise RuntimeError(f"Image URL returned HTTP {resp.status_code}.")
        mime = (resp.headers.get("Content-Type") or "application/octet-stream").split(";")[0].strip().lower()
        chunks: list[bytes] = []
        for chunk in resp.iter_content(chunk_size=65536):
            if chunk:
                chunks.append(chunk)
    finally:
        resp.close()
    data = b"".join(chunks)
    if not data:
        raise RuntimeError("Image body is empty.")
    return data, mime


def load_image(path: str) -> tuple[str, bytes, str]:
    ext = path.split(".")[-1].lower()
    if ext in ["jpg", "jpeg"]:
        mime = "image/jpeg"
    elif ext == "webp":
        mime = "image/webp"
    else:
        mime = "image/png"
    with open(path, "rb") as f:
        return (os.path.basename(path), f.read(), mime)


def load_image_source(src: str) -> tuple[str, bytes, str]:
    """Local path or http(s) URL → (filename, bytes, mime)."""
    s = (src or "").strip()
    if s.startswith(("http://", "https://")):
        data, mime = _download_url_binary(s)
        ext = "jpg" if "jpeg" in mime else "png"
        if "webp" in mime:
            ext = "webp"
        return (f"remote.{ext}", data, mime)
    return load_image(s)


def extract_json(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise ValueError("JSON parse failed") from None


def detect_image_roles(images: list[str], client: OpenAI) -> list[dict[str, Any]]:
    _logger.info("detecting roles...")
    content: list[dict[str, Any]] = []
    for img in images:
        name, data, mime = load_image_source(img)
        b64 = base64.b64encode(data).decode()
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            }
        )

    res = client.chat.completions.create(
        model=TEXT_MODEL,
        messages=[
            {
                "role": "system",
                "content": """
Return ONLY JSON.

Two valid formats allowed:

Format A:
{
  "roles": [
    {"image_index": 0, "role": "PRODUCT"},
    {"image_index": 1, "role": "TEMPLATE"}
  ]
}

Format B:
{
  "product": 0,
  "template": 1,
  "logo": 2
}
""",
            },
            {"role": "user", "content": content},
        ],
        temperature=0,
    )

    raw = res.choices[0].message.content or ""
    _logger.info("RAW ROLE OUTPUT: {}", raw[:500])
    data = extract_json(raw)

    if "roles" in data:
        return list(data["roles"])

    roles: list[dict[str, Any]] = []
    if "product" in data:
        roles.append({"image_index": data["product"], "role": "PRODUCT"})
    if "template" in data:
        roles.append({"image_index": data["template"], "role": "TEMPLATE"})
    if "logo" in data:
        roles.append({"image_index": data["logo"], "role": "LOGO"})

    if not roles:
        _logger.warning("role detection failed → fallback")
        return [
            {"image_index": 0, "role": "PRODUCT"},
            {"image_index": 1, "role": "TEMPLATE"},
            {"image_index": 2, "role": "LOGO"},
        ]

    return roles


def reorder_images(images: list[str], roles: list[dict[str, Any]]) -> list[str]:
    product = None
    template = None
    logo = None
    for r in roles:
        role = r.get("role")
        idx = int(r.get("image_index", -1))
        if idx < 0 or idx >= len(images):
            continue
        if role == "PRODUCT":
            product = images[idx]
        elif role == "TEMPLATE":
            template = images[idx]
        elif role == "LOGO":
            logo = images[idx]

    ordered: list[str] = []
    if product:
        ordered.append(product)
    if template:
        ordered.append(template)
    if logo:
        ordered.append(logo)
    return ordered if ordered else images


def build_ad_prompt(user_request: str, client: OpenAI) -> str:
    res = client.chat.completions.create(
        model=TEXT_MODEL,
        messages=[
            {
                "role": "system",
                "content": """
You are a professional advertising AI.

Convert user request into strict compositing instructions.

Rules:
- Preserve template layout
- Keep product unchanged
- Decide layout type (split / replace)
- No hallucination
- No unnecessary objects
""",
            },
            {"role": "user", "content": user_request},
        ],
        temperature=0,
    )
    return (res.choices[0].message.content or "").strip()


def generate_image_edit(images: list[str], prompt: str, openai_api_key: str) -> tuple[bytes, str]:
    files = [load_image_source(i) for i in images]
    return generate_image_with_openai_edit_multi(
        prompt=prompt,
        image_files=files,
        openai_api_key=openai_api_key,
        model=IMAGE_MODEL,
        size=AD_IMAGE_SIZE,
    )


def generate_from_text(user_request: str, openai_api_key: str) -> tuple[bytes, str]:
    _logger.info("generate mode (internal refine)")
    client = OpenAI(api_key=resolve_openai_key(openai_api_key))
    res_prompt = client.chat.completions.create(
        model=TEXT_MODEL,
        messages=[
            {
                "role": "system",
                "content": """
You are a high-end advertising prompt engineer.

Convert user request into a premium image generation prompt.

Rules:
- photorealistic
- premium product ad style
- minimal clutter
- strong lighting description
- clear composition
- Instagram 4:5 layout
""",
            },
            {"role": "user", "content": user_request},
        ],
        temperature=0,
    )
    final_prompt = (res_prompt.choices[0].message.content or "").strip()
    _logger.info("FINAL PROMPT:\n{}", final_prompt[:800])
    return generate_image_with_openai_text(
        final_prompt,
        openai_api_key=openai_api_key,
        size=AD_IMAGE_SIZE,
        model=IMAGE_MODEL,
    )


def generate_hybrid(
    images: list[str],
    user_request: str,
    *,
    raw_prompt_is_final: bool,
    openai_api_key: str,
) -> tuple[bytes, str]:
    name, data, _mime = load_image_source(images[0])
    _ = name
    if raw_prompt_is_final:
        prompt = user_request.strip()
    else:
        prompt = f"""
Use the given product.

Create a realistic scene.

{user_request}

Rules:
- product unchanged
- correct scale
- natural lighting
"""
    return generate_image_with_openai_edit(
        prompt=prompt,
        reference_image_bytes=data,
        openai_api_key=openai_api_key,
        size=AD_IMAGE_SIZE,
        model=IMAGE_MODEL,
    )


def generate_caption(images: list[str], user_request: str | None, client: OpenAI) -> str:
    content: list[dict[str, Any]] = []
    for img in images:
        name, data, mime = load_image_source(img)
        b64 = base64.b64encode(data).decode()
        content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})

    text = f"""
User intent:
{user_request or "none"}

Generate Instagram caption + hashtags.
"""

    res = client.chat.completions.create(
        model=TEXT_MODEL,
        messages=[
            {"role": "system", "content": "You are a social media expert."},
            {"role": "user", "content": content + [{"type": "text", "text": text}]},
        ],
        temperature=0.7,
    )
    return (res.choices[0].message.content or "").strip()


def decide_mode(images: list[str], user_request: str) -> str:
    if images and len(images) >= 2:
        return "edit"
    if images and len(images) == 1:
        return "hybrid"
    if user_request.strip():
        return "generate"
    return "noop"


def generate_ad(
    images: list[str] | None,
    user_request: str,
    openai_api_key: str | None = None,
    *,
    raw_prompt_is_final: bool = False,
    size_override: str | None = None,
) -> dict[str, Any]:
    """Master entry (``yedek2.generate_ad``).

    Args:
        images: Local paths or http(s) URLs.
        user_request: User text; when *raw_prompt_is_final* is True (API path), passed
            unchanged to the image API as the final English prompt.
        raw_prompt_is_final: If True, skip internal prompt refinement (preserve CIS/PromptBuilder output).

    Returns:
        ``{"image_bytes": bytes, "mime": str, "caption": str | None}``
    """
    _logger.info("running generate_ad pipeline raw_prompt_is_final={}", raw_prompt_is_final)
    key = resolve_openai_key(openai_api_key)
    client = OpenAI(api_key=key)

    images = images or []
    user_request = user_request or ""

    mode = decide_mode(images, user_request)
    _logger.info("MODE: {}", mode)

    image_bytes: bytes | None = None
    mime = "image/png"
    caption: str | None = None

    if mode == "noop":
        raise ValueError("generate_ad: nothing to do (no images and empty request).")

    if mode == "edit":
        roles = detect_image_roles(images, client)
        images = reorder_images(images, roles)
        edit_prompt = user_request.strip() if raw_prompt_is_final else build_ad_prompt(user_request, client)
        image_bytes, mime = generate_image_edit(images, edit_prompt, key)

    elif mode == "hybrid":
        image_bytes, mime = generate_hybrid(
            images,
            user_request,
            raw_prompt_is_final=raw_prompt_is_final,
            openai_api_key=key,
        )

    elif mode == "generate":
        target_size = (size_override or "").strip() or AD_IMAGE_SIZE
        if raw_prompt_is_final:
            image_bytes, mime = generate_image_with_openai_text(
                user_request.strip(),
                openai_api_key=key,
                size=target_size,
                model=IMAGE_MODEL,
            )
        else:
            image_bytes, mime = generate_from_text(user_request, key)

    if "caption" in user_request.lower() or (images and not user_request.strip()):
        try:
            caption = generate_caption(images, user_request, client)
        except Exception as exc:
            _logger.warning("caption generation skipped: {}", exc)

    if image_bytes is None:
        raise RuntimeError("generate_ad produced no image.")

    return {"image_bytes": image_bytes, "mime": mime, "caption": caption}
