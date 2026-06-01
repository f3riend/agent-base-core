"""ContentIntelligenceService — single GPT-4o call (with optional vision) that returns
a fully-populated ContentContext describing scene, intent, enriched English image prompt,
and Turkish caption brief.

Stateless: no instance variables, safe to call from multiple threads / Celery workers.
"""

from __future__ import annotations

import base64
import json
import os
import re

import requests
from loguru import logger
from openai import OpenAI

from app.schemas.content import ContentContext

_logger = logger.bind(module="content-intelligence")

# Vision model — gpt-4o (not mini) for quality multimodal analysis
_VISION_MODEL = "gpt-4o"
_TEXT_MODEL = "gpt-4o"
_MAX_REF_BYTES = 10 * 1024 * 1024  # 10 MiB

# ---------------------------------------------------------------------------
# Platform composition rules
# ---------------------------------------------------------------------------

_PLATFORM_RULES: dict[str, str] = {
    "feed": "square 1:1 composition, rich detail, strong focal point",
    "story": "vertical 9:16 composition, single bold message, minimal elements",
    "video": "motion-friendly scene, clear subject with room to animate, dynamic potential",
}

# ---------------------------------------------------------------------------
# Physics hint mapping: keyword → hint sentence
# ---------------------------------------------------------------------------

_PHYSICS_RULES: list[tuple[list[str], str]] = [
    (
        ["liquid", "water", "coffee", "drink", "pour", "juice", "tea", "bira", "su", "kahve", "içecek", "sıvı"],
        "Liquid: correct meniscus, coherent pour arc, proper transparency",
    ),
    (
        ["hand", "person", "human", "el", "insan", "kişi", "adam", "kadın", "finger", "parmak"],
        "Hands: anatomically correct, exactly 5 fingers",
    ),
    (
        ["cable", "wire", "charger", "kablo", "tel", "şarj"],
        "Cables: continuous path from port to plug, no floating segments",
    ),
    (
        ["glass", "cam", "crystal", "kristal", "şişe", "bottle"],
        "Glass: correct refraction, consistent transparency",
    ),
    (
        ["text", "badge", "label", "yazı", "etiket", "logo", "price", "fiyat", "tag"],
        "Text/badges: planar, legible, aligned to surface perspective",
    ),
]


def _detect_physics_hints(text: str) -> list[str]:
    """Return relevant physics hints by scanning *text* for trigger keywords."""
    text_lower = (text or "").lower()
    hints: list[str] = []
    for keywords, hint in _PHYSICS_RULES:
        if any(kw in text_lower for kw in keywords):
            hints.append(hint)
    return hints


# ---------------------------------------------------------------------------
# Image download helper
# ---------------------------------------------------------------------------


def _download_image_as_b64(url: str) -> tuple[str, str]:
    """Download image from *url*, return (base64_string, mime_type).

    Raises RuntimeError on network failure or non-image content-type.
    """
    url = (url or "").strip()
    if not url:
        raise RuntimeError("ContentIntelligenceService: reference_image_url is empty.")
    try:
        resp = requests.get(url, timeout=30, stream=True)
    except requests.RequestException as exc:
        raise RuntimeError(f"ContentIntelligenceService: reference image download failed — {exc}") from exc

    if resp.status_code >= 400:
        raise RuntimeError(
            f"ContentIntelligenceService: reference image URL returned HTTP {resp.status_code}."
        )

    mime = (resp.headers.get("Content-Type") or "image/jpeg").split(";")[0].strip().lower()
    if not mime.startswith("image/"):
        raise RuntimeError(
            f"ContentIntelligenceService: URL did not return image Content-Type (got '{mime}')."
        )

    chunks: list[bytes] = []
    total = 0
    try:
        for chunk in resp.iter_content(chunk_size=65536):
            if not chunk:
                continue
            total += len(chunk)
            if total > _MAX_REF_BYTES:
                raise RuntimeError("ContentIntelligenceService: reference image exceeds 10 MiB limit.")
            chunks.append(chunk)
    finally:
        resp.close()

    data = b"".join(chunks)
    if not data:
        raise RuntimeError("ContentIntelligenceService: reference image body is empty.")

    return base64.b64encode(data).decode("utf-8"), mime


# ---------------------------------------------------------------------------
# System prompt builder
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_TEMPLATE = """\
You are a professional visual content strategist and art director.

Your task:
1. Analyse the user's prompt (and the attached reference image if provided).
2. Return ONLY a single JSON object — no markdown fences, no extra text.

The JSON must exactly match this schema:
{{
  "scene_subject": "<string or null>",
  "scene_lighting": "<string or null>",
  "scene_background": "<string or null>",
  "scene_mood": "<string or null>",
  "scene_color_palette": "<string or null>",
  "scene_composition": "<string or null>",
  "intent_category": "<one of: product_showcase | lifestyle | announcement | engagement | informational>",
  "intent_summary": "<1-2 sentence summary of what the post is trying to achieve>",
  "target_platform": "{platform}",
  "refined_image_prompt_en": "<Professional English image prompt, 2-4 sentences, specific and visual. Platform composition rule: {platform_rule}>",
  "refined_caption_tr": "<Enriched Turkish brief for the caption writer — NOT the final caption, just the enriched version of the input>",
  "relevant_physics_hints": [],
  "intent_confidence": 0.95,
  "needs_clarification": false,
  "clarification_question": null
}}

Rules:
- Scene fields (scene_*) should be null if no reference image is provided.
- refined_image_prompt_en must be entirely in English. Never mix Turkish.
- refined_caption_tr must be entirely in Turkish. Never mix English.
- relevant_physics_hints: include ONLY the hints that are actually relevant to the subject; do not include irrelevant ones.
- If the prompt is too vague to generate good output, set needs_clarification to true and fill clarification_question.
- Do NOT wrap the output in ```json fences.
"""


def _build_system_prompt(platform: str) -> str:
    rule = _PLATFORM_RULES.get(platform, _PLATFORM_RULES["feed"])
    return _SYSTEM_PROMPT_TEMPLATE.format(platform=platform, platform_rule=rule)


def _strip_json_fences(raw: str) -> str:
    """Remove optional ```json fences and trim whitespace."""
    text = re.sub(r"^```(?:json)?\s*", "", raw or "")
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Public service class
# ---------------------------------------------------------------------------


class ContentIntelligenceService:
    """Stateless service. No instance variables — safe for concurrent use."""

    def analyze(
        self,
        user_prompt: str,
        reference_image_url: str | None = None,
        platform: str = "feed",
        openai_api_key: str | None = None,
    ) -> ContentContext:
        """Analyse *user_prompt* + optional *reference_image_url*.

        Returns a :class:`ContentContext` with enriched English image prompt
        and Turkish caption brief.

        Args:
            user_prompt: Raw user prompt (may be Turkish or mixed).
            reference_image_url: Optional URL of a reference image.
            platform: One of ``"feed"``, ``"story"``, or ``"video"``.
            openai_api_key: OpenAI API key; falls back to ``OPENAI_API_KEY`` env var.

        Raises:
            RuntimeError: On API failure or JSON parse error.
        """
        api_key = (openai_api_key or "").strip() or (os.getenv("OPENAI_API_KEY") or "").strip()
        if not api_key:
            raise RuntimeError(
                "ContentIntelligenceService: OpenAI API key required (OPENAI_API_KEY)."
            )

        platform = platform if platform in ("feed", "story", "video") else "feed"
        system_prompt = _build_system_prompt(platform)

        # Build user message content
        user_content: list[dict] = []

        # Text part
        prompt_text = (user_prompt or "").strip()
        if not prompt_text:
            raise RuntimeError("ContentIntelligenceService: user_prompt must not be empty.")

        user_content.append({"type": "text", "text": f"User prompt:\n{prompt_text}"})

        # Image part (vision)
        if reference_image_url:
            _logger.debug("Downloading reference image for vision analysis: {}", reference_image_url[:80])
            b64, mime = _download_image_as_b64(reference_image_url)
            user_content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}", "detail": "high"},
                }
            )

        # Detect physics hints from raw prompt (before API call) and add as context
        pre_hints = _detect_physics_hints(prompt_text)
        if pre_hints:
            user_content.append(
                {
                    "type": "text",
                    "text": (
                        "Physics hints to consider (include only the ones relevant to this scene):\n"
                        + "\n".join(f"- {h}" for h in pre_hints)
                    ),
                }
            )

        _logger.info(
            "Calling GPT-4o vision={} platform={} prompt_len={}",
            reference_image_url is not None,
            platform,
            len(prompt_text),
        )

        client = OpenAI(api_key=api_key)
        try:
            response = client.chat.completions.create(
                model=_VISION_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.3,
                max_tokens=1200,
            )
        except Exception as exc:
            raise RuntimeError(
                f"ContentIntelligenceService: GPT-4o call failed — {exc}"
            ) from exc

        raw = (response.choices[0].message.content or "").strip() if response.choices else ""
        if not raw:
            raise RuntimeError("ContentIntelligenceService: GPT-4o returned empty response.")

        # Strip ```json fences if present
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        raw = raw.strip()

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"ContentIntelligenceService: JSON parse failed — {exc}. Raw response: {raw[:400]}"
            ) from exc

        # Merge pre-detected physics hints with any the model returned
        model_hints: list[str] = data.get("relevant_physics_hints") or []
        merged_hints = list(dict.fromkeys(pre_hints + [h for h in model_hints if h]))

        data["relevant_physics_hints"] = merged_hints
        data["target_platform"] = platform  # always trust our own value

        try:
            ctx = ContentContext(**data)
        except Exception as exc:
            raise RuntimeError(
                f"ContentIntelligenceService: ContentContext validation failed — {exc}. "
                f"Raw data: {str(data)[:400]}"
            ) from exc

        _logger.info(
            "ContentContext built: category={} confidence={} hints={}",
            ctx.intent_category,
            ctx.intent_confidence,
            len(ctx.relevant_physics_hints),
        )
        return ctx

    def extract_label_info(
        self,
        reference_image_url: str,
        openai_api_key: str | None = None,
    ) -> dict[str, object]:
        """Extract label OCR + style metadata from a reference product image.

        Returns a dict with keys:
        ``brand_name``, ``product_name``, ``label_texts``, ``label_style``,
        ``label_colors``, ``label_layout``.
        """
        api_key = (openai_api_key or "").strip() or (os.getenv("OPENAI_API_KEY") or "").strip()
        if not api_key:
            raise RuntimeError("Etiket OCR icin OpenAI API anahtari gerekli.")

        image_url = (reference_image_url or "").strip()
        if not image_url:
            raise RuntimeError("Etiket OCR icin referans gorsel URL bos olamaz.")

        try:
            b64, mime = _download_image_as_b64(image_url)
        except Exception as exc:
            raise RuntimeError(f"Referans gorsel indirilemedi: {exc}") from exc

        system_prompt = (
            "You are an OCR and packaging-layout extractor.\n"
            "Return ONLY valid JSON. Do not include markdown, comments, or explanations.\n"
            "Schema:\n"
            "{\n"
            '  "brand_name": "string",\n'
            '  "product_name": "string",\n'
            '  "label_texts": ["line1", "line2"],\n'
            '  "label_style": "string",\n'
            '  "label_colors": ["color1", "color2"],\n'
            '  "label_layout": "string"\n'
            "}\n"
            "If a field is unknown, return empty string or empty array."
        )

        client = OpenAI(api_key=api_key)
        try:
            response = client.chat.completions.create(
                model=_VISION_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Gorseldeki urun etiketini OCR et ve JSON olarak don."},
                            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}", "detail": "high"}},
                        ],
                    },
                ],
                temperature=0.1,
                max_tokens=900,
            )
        except Exception as exc:
            raise RuntimeError(f"Etiket OCR analizi basarisiz oldu: {exc}") from exc

        raw = (response.choices[0].message.content or "").strip() if response.choices else ""
        if not raw:
            raise RuntimeError("Etiket OCR yaniti bos dondu.")

        cleaned = _strip_json_fences(raw)
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Etiket OCR JSON parse hatasi: {exc}") from exc

        if not isinstance(parsed, dict):
            raise RuntimeError("Etiket OCR cikti formati gecersiz (JSON object bekleniyordu).")

        label_texts = parsed.get("label_texts")
        label_colors = parsed.get("label_colors")

        return {
            "brand_name": str(parsed.get("brand_name") or "").strip(),
            "product_name": str(parsed.get("product_name") or "").strip(),
            "label_texts": [str(x).strip() for x in (label_texts or []) if str(x).strip()] if isinstance(label_texts, list) else [],
            "label_style": str(parsed.get("label_style") or "").strip(),
            "label_colors": [str(x).strip() for x in (label_colors or []) if str(x).strip()] if isinstance(label_colors, list) else [],
            "label_layout": str(parsed.get("label_layout") or "").strip(),
        }
