"""OpenAI / FAL üretim modelleri için USD fiyat sabitleri.

Modeller veya fiyatlar değiştiğinde sadece burayı güncelle.
"""

from __future__ import annotations

TOKEN_PRICING: dict[str, dict[str, float]] = {
    "gpt-4o-mini": {"input": 0.00015 / 1000, "output": 0.0006 / 1000},
    "gpt-4o": {"input": 0.005 / 1000, "output": 0.015 / 1000},
}

IMAGE_PRICING: dict[str, float] = {
    "gpt-image-1": 0.04,
}

VIDEO_PRICING: dict[str, float] = {
    "fal-kling-v3": 0.40,
}


def caption_cost(model: str, input_tokens: int | None, output_tokens: int | None) -> float:
    p = TOKEN_PRICING.get(model)
    if not p:
        return 0.0
    return round((input_tokens or 0) * p["input"] + (output_tokens or 0) * p["output"], 6)


def image_cost(model: str, count: int | None) -> float:
    unit = IMAGE_PRICING.get(model, 0.0)
    return round(unit * (count or 0), 6)


def video_cost(model: str) -> float:
    return round(VIDEO_PRICING.get(model, 0.0), 6)


def compute_cost(
    kind: str,
    model: str,
    *,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    image_count: int | None = None,
) -> float:
    if kind in ("caption", "caption_revize"):
        return caption_cost(model, input_tokens, output_tokens)
    if kind in ("image", "image_reference", "image_revise"):
        return image_cost(model, image_count or 1)
    if kind == "video":
        return video_cost(model)
    if kind == "holiday":
        cap = caption_cost(model, input_tokens, output_tokens)
        return cap if cap else video_cost(model)
    return 0.0
