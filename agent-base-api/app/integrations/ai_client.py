import base64
import io
import os
import time
from typing import Any

import requests
from openai import OpenAI
from PIL import Image

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_TEXT_MAX_RETRIES = 4
_IMAGE_MAX_RETRIES = 4
_BASE_BACKOFF_SECONDS = 1.0
_FAL_IMAGE_MODEL = "fal-ai/flux-pro/v1.1-ultra"
_FAL_IMAGE_REF_MODEL = "fal-ai/flux-pro/v1.1-ultra/redux"
_FAL_BG_REPLACE_MODEL = "fal-ai/bria/background/replace"
_FAL_PRODUCT_SHOT_MODEL = "fal-ai/bria/product-shot"
_FAL_BIREFNET_MODEL = "fal-ai/birefnet"
# Kling 3.0 Pro (fal.ai) — v1.6 path removed; use v3 text / image endpoints.
_FAL_KLING_V3_PRO_TEXT_TO_VIDEO = "fal-ai/kling-video/v3/pro/text-to-video"
_FAL_KLING_V3_PRO_IMAGE_TO_VIDEO = "fal-ai/kling-video/v3/pro/image-to-video"
_OPENAI_TEXT_MODEL = "gpt-4o-mini"
_OPENAI_IMAGE_MODEL = "gpt-image-2"
_FAL_REF_STRENGTH_EDIT = 0.78    # revision: preserve product silhouette, apply feedback


def resolve_fal_key(fal_api_key: str | None = None) -> str:
    key = (fal_api_key or "").strip() or (os.getenv("FAL_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("fal.ai API anahtari gerekli (FAL_API_KEY).")
    return key


def resolve_openai_key(openai_api_key: str | None = None) -> str:
    key = (openai_api_key or "").strip() or (os.getenv("OPENAI_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("OpenAI API anahtari gerekli (OPENAI_API_KEY).")
    return key


def resolve_gemini_key(gemini_api_key: str | None = None) -> str:
    # Legacy shim used by manager agent path.
    key = (gemini_api_key or "").strip() or (os.getenv("GEMINI_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("Gemini API anahtari gerekli (GEMINI_API_KEY).")
    return key


def _fal_headers(fal_api_key: str) -> dict[str, str]:
    return {"Authorization": f"Key {fal_api_key}", "Content-Type": "application/json"}


def _fal_submit_and_wait(
    *,
    model: str,
    input_payload: dict[str, Any],
    fal_api_key: str,
    max_retries: int,
) -> dict[str, Any]:
    base_url = f"https://queue.fal.run/{model}"
    headers = _fal_headers(fal_api_key)

    # 1. Submit
    submit_resp = requests.post(base_url, headers=headers, json=input_payload, timeout=90)
    if submit_resp.status_code >= 400:
        raise RuntimeError(f"fal.ai submit hatasi ({model}): HTTP {submit_resp.status_code} - {submit_resp.text[:220]}")

    submit_data = submit_resp.json()
    request_id = str(submit_data.get("request_id") or "").strip()
    status_url = str(submit_data.get("status_url") or "").strip()
    response_url = str(submit_data.get("response_url") or "").strip()

    if not request_id:
        raise RuntimeError(f"fal.ai request_id yok ({model}).")

    # status_url yoksa kendimiz oluştur
    if not status_url:
        status_url = f"https://queue.fal.run/{model}/requests/{request_id}/status"
    if not response_url:
        response_url = f"https://queue.fal.run/{model}/requests/{request_id}"

    # 2. Status polling — COMPLETED olana kadar bekle
    for attempt in range(max_retries * 3):  # daha fazla deneme
        time.sleep(2.0 + attempt * 0.5)  # ilk denemede de bekle
        poll = requests.get(status_url, headers=headers, timeout=90)
        if poll.status_code >= 400:
            raise RuntimeError(f"fal.ai status hatasi ({model}): HTTP {poll.status_code} - {poll.text[:220]}")
        data = poll.json()
        status = str(data.get("status") or "").upper()
        if status in {"IN_QUEUE", "IN_PROGRESS", "PROCESSING"}:
            continue
        if status in {"COMPLETED", "SUCCESS", "DONE"}:
            break
        if status in {"FAILED", "ERROR", "CANCELLED"}:
            raise RuntimeError(f"fal.ai islem basarisiz ({model}): {data.get('error') or data}")
    else:
        raise RuntimeError(f"fal.ai timeout ({model}).")

    # 3. Sonucu response_url'den al
    result_resp = requests.get(response_url, headers=headers, timeout=90)
    if result_resp.status_code >= 400:
        raise RuntimeError(f"fal.ai result hatasi ({model}): HTTP {result_resp.status_code} - {result_resp.text[:220]}")
    result = result_resp.json()

    # Bazı modeller output wrapper kullanır, bazıları direkt root'a koyar.
    # Kling v3 text/image-to-video: kökte veya data altında ``video: { url }`` döner (root'ta ``url`` olmayabilir).
    output = result.get("output")
    if isinstance(output, dict) and output:
        return output

    if isinstance(result, dict):
        data = result.get("data")
        if isinstance(data, dict) and (
            data.get("video") is not None
            or data.get("video_url")
            or data.get("url")
            or data.get("images")
            or data.get("image_url")
        ):
            return data

    if isinstance(result, dict) and (
        "images" in result
        or "image_url" in result
        or "url" in result
        or "video" in result
        or "video_url" in result
    ):
        return result

    raise RuntimeError(f"fal.ai output bos ({model}).")

def _download_binary(url: str) -> tuple[bytes, str]:
    resp = requests.get(url, timeout=120)
    if resp.status_code >= 400:
        raise RuntimeError(f"Asset indirilemedi: HTTP {resp.status_code}")
    content = resp.content or b""
    if not content:
        raise RuntimeError("Asset bos dondu.")
    mime = (resp.headers.get("Content-Type") or "application/octet-stream").split(";")[0].strip().lower()
    # Some upstream CDNs return application/octet-stream even for images.
    # Instagram requires a real media type, so infer from file signature.
    if mime in {"", "application/octet-stream"}:
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            mime = "image/png"
        elif content.startswith(b"\xff\xd8\xff"):
            mime = "image/jpeg"
        elif content.startswith(b"GIF87a") or content.startswith(b"GIF89a"):
            mime = "image/gif"
        elif len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
            mime = "image/webp"
    return content, mime


def _extract_image_url(output: dict[str, Any]) -> str:
    images = output.get("images")
    if isinstance(images, list) and images:
        first = images[0]
        if isinstance(first, dict):
            url = str(first.get("url") or first.get("image_url") or "").strip()
            if url:
                return url
    for key in ("image_url", "url"):
        url = str(output.get(key) or "").strip()
        if url:
            return url
    raise RuntimeError("fal.ai gorsel URL donmedi.")


def generate_text(prompt: str, gemini_api_key: str | None = None, model: str = "gemini-2.5-flash") -> str:
    _ = model
    client = OpenAI(api_key=resolve_openai_key(gemini_api_key))
    last_error: str | None = None
    for attempt in range(_TEXT_MAX_RETRIES):
        try:
            resp = client.chat.completions.create(
                model=_OPENAI_TEXT_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
            )
            text = (resp.choices[0].message.content or "").strip() if resp.choices else ""
            if text:
                return text
            last_error = "no_text_in_response"
            break
        except Exception as exc:
            last_error = str(exc)
            if attempt < _TEXT_MAX_RETRIES - 1:
                time.sleep(_BASE_BACKOFF_SECONDS * (2**attempt))
                continue
            break
    raise RuntimeError(f"OpenAI text hatasi: {last_error or 'unknown'}")


def generate_image_bytes(
    prompt: str,
    gemini_api_key: str | None = None,
    *,
    image_model: str | None = None,
    fal_api_key: str | None = None,
) -> tuple[bytes, str]:
    model = (image_model or "").strip() or _FAL_IMAGE_MODEL
    key = resolve_fal_key(fal_api_key or gemini_api_key)
    output = _fal_submit_and_wait(
        model=model,
        input_payload={"prompt": prompt, "output_format": "png"},
        fal_api_key=key,
        max_retries=_IMAGE_MAX_RETRIES,
    )
    image_url = _extract_image_url(output)
    return _download_binary(image_url)


def generate_image_with_reference_bytes(
    prompt: str,
    reference_image_bytes: bytes,
    reference_mime_type: str = "image/jpeg",
    gemini_api_key: str | None = None,
    *,
    image_model: str | None = None,
    fal_api_key: str | None = None,
    edit_mode: bool = False,
) -> tuple[bytes, str]:
    model = (image_model or "").strip() or _FAL_IMAGE_REF_MODEL
    key = resolve_fal_key(fal_api_key or gemini_api_key)
    ref_b64 = base64.b64encode(reference_image_bytes).decode("utf-8")
    ref_url = f"data:{reference_mime_type};base64,{ref_b64}"
    prompt_strength = _FAL_REF_STRENGTH_EDIT if edit_mode else _FAL_REF_STRENGTH_ANCHOR
    output = _fal_submit_and_wait(
        model=model,
        input_payload={
            "prompt": prompt,
            "image_url": ref_url,
            "image_prompt_strength": prompt_strength,
            "enhance_prompt": False,
            "output_format": "png",
        },
        fal_api_key=key,
        max_retries=_IMAGE_MAX_RETRIES,
    )
    image_url = _extract_image_url(output)
    return _download_binary(image_url)


def _to_rgba_png_bytes(image_bytes: bytes) -> bytes:
    """Convert arbitrary image bytes to RGBA PNG (required by OpenAI image edit API)."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def generate_image_with_openai_text(
    prompt: str,
    openai_api_key: str | None = None,
    *,
    size: str = "1024x1280",
    model: str | None = None,
) -> tuple[bytes, str]:
    """Generate an image from text-only using OpenAI image API (default ``gpt-image-2``).

    Returns:
        (image_bytes, "image/png")
    """
    key = resolve_openai_key(openai_api_key)
    client = OpenAI(api_key=key)
    image_model = (model or "").strip() or _OPENAI_IMAGE_MODEL
    last_error: str | None = None
    for attempt in range(_IMAGE_MAX_RETRIES):
        try:
            response = client.images.generate(
                model=image_model,
                prompt=prompt,
                n=1,
                size=size,
            )
            image_data = response.data[0] if response.data else None
            if not image_data:
                raise RuntimeError("OpenAI image generate bos veri dondu.")
            if getattr(image_data, "b64_json", None):
                raw = base64.b64decode(image_data.b64_json)
                return raw, "image/png"
            if getattr(image_data, "url", None):
                return _download_binary(image_data.url)
            raise RuntimeError("OpenAI image generate: URL ve b64_json ikisi de yok.")
        except Exception as exc:
            last_error = str(exc)
            if attempt < _IMAGE_MAX_RETRIES - 1:
                time.sleep(_BASE_BACKOFF_SECONDS * (2**attempt))
                continue
            break
    raise RuntimeError(f"OpenAI image generate hatasi: {last_error or 'unknown'}")


def generate_image_with_openai_edit(
    prompt: str,
    reference_image_bytes: bytes,
    openai_api_key: str | None = None,
    *,
    size: str = "1024x1280",
    model: str | None = None,
) -> tuple[bytes, str]:
    """Generate a lifestyle / product image using OpenAI image edit API (default ``gpt-image-2``).

    Takes the reference product image and generates a new scene around it
    following *prompt*.  GPT-4o's image understanding keeps the product
    appearance faithful while seamlessly integrating it into the scene.

    Args:
        prompt: English scene description (product identity instructions are
                prepended automatically).
        reference_image_bytes: Raw bytes of the product image (any common format).
        openai_api_key: OpenAI API key; falls back to OPENAI_API_KEY env var.
        size: Output image size — ``"1024x1024"``, ``"1024x1536"``, etc.

    Returns:
        (image_bytes, "image/png")
    """
    key = resolve_openai_key(openai_api_key)
    client = OpenAI(api_key=key)
    image_model = (model or "").strip() or _OPENAI_IMAGE_MODEL

    # OpenAI edit API requires RGBA PNG
    png_bytes = _to_rgba_png_bytes(reference_image_bytes)
    image_file = io.BytesIO(png_bytes)
    image_file.name = "product.png"

    full_prompt = (
        "IMPORTANT: The reference image shows a specific product. "
        "Keep that product EXACTLY as it is — same shape, colors, labels, "
        "branding, materials, and proportions. Do NOT redesign or replace it. "
        f"Now generate this scene around/with the product: {prompt.strip()}"
    )

    last_error: str | None = None
    for attempt in range(_IMAGE_MAX_RETRIES):
        try:
            image_file.seek(0)
            response = client.images.edit(
                model=image_model,
                image=image_file,
                prompt=full_prompt,
                n=1,
                size=size,
            )
            image_data = response.data[0] if response.data else None
            if not image_data:
                raise RuntimeError("OpenAI image edit bos veri dondu.")

            if getattr(image_data, "b64_json", None):
                raw = base64.b64decode(image_data.b64_json)
                return raw, "image/png"

            if getattr(image_data, "url", None):
                return _download_binary(image_data.url)

            raise RuntimeError("OpenAI image edit: URL ve b64_json ikisi de yok.")

        except Exception as exc:
            last_error = str(exc)
            if attempt < _IMAGE_MAX_RETRIES - 1:
                time.sleep(_BASE_BACKOFF_SECONDS * (2 ** attempt))
                continue
            break

    raise RuntimeError(f"OpenAI image edit hatasi: {last_error or 'unknown'}")


def generate_image_with_openai_edit_multi(
    prompt: str,
    image_files: list[tuple[str, bytes, str]],
    openai_api_key: str | None = None,
    *,
    size: str = "1024x1280",
    model: str | None = None,
) -> tuple[bytes, str]:
    """Edit / composite using multiple reference images (same idea as ``yedek2.generate_image_edit``).

    Each tuple is ``(filename, raw_bytes, mime_type)``. All inputs are normalized to RGBA PNG.
    """
    if not image_files:
        raise ValueError("image_files must not be empty.")
    key = resolve_openai_key(openai_api_key)
    client = OpenAI(api_key=key)
    image_model = (model or "").strip() or _OPENAI_IMAGE_MODEL

    io_files: list[io.BytesIO] = []
    for name, raw_bytes, _mime in image_files:
        png_bytes = _to_rgba_png_bytes(raw_bytes)
        buf = io.BytesIO(png_bytes)
        buf.name = (name or "layer.png").strip() or "layer.png"
        io_files.append(buf)

    last_error: str | None = None
    for attempt in range(_IMAGE_MAX_RETRIES):
        try:
            for f in io_files:
                f.seek(0)
            image_arg: Any = io_files[0] if len(io_files) == 1 else io_files
            response = client.images.edit(
                model=image_model,
                image=image_arg,
                prompt=prompt.strip(),
                n=1,
                size=size,
            )
            image_data = response.data[0] if response.data else None
            if not image_data:
                raise RuntimeError("OpenAI image edit bos veri dondu.")
            if getattr(image_data, "b64_json", None):
                raw = base64.b64decode(image_data.b64_json)
                return raw, "image/png"
            if getattr(image_data, "url", None):
                return _download_binary(image_data.url)
            raise RuntimeError("OpenAI image edit: URL ve b64_json ikisi de yok.")
        except Exception as exc:
            last_error = str(exc)
            if attempt < _IMAGE_MAX_RETRIES - 1:
                time.sleep(_BASE_BACKOFF_SECONDS * (2**attempt))
                continue
            break
    raise RuntimeError(f"OpenAI image edit (multi) hatasi: {last_error or 'unknown'}")


def extract_product_cutout(
    image_url: str,
    fal_api_key: str | None = None,
) -> tuple[bytes, str]:
    """Remove background from *image_url*, returning a transparent PNG of the product.

    Uses fal-ai/birefnet for high-quality foreground segmentation.
    The returned bytes are a PNG with an alpha channel (RGBA).

    Args:
        image_url: Publicly accessible URL of the product image.
        fal_api_key: fal.ai API key; falls back to FAL_API_KEY env var.

    Returns:
        (png_bytes, "image/png") — the product on a transparent background.
    """
    key = resolve_fal_key(fal_api_key)
    output = _fal_submit_and_wait(
        model=_FAL_BIREFNET_MODEL,
        input_payload={"image_url": image_url},
        fal_api_key=key,
        max_retries=_IMAGE_MAX_RETRIES,
    )
    # BiRefNet returns {"image": {"url": "..."}}
    image_data = output.get("image")
    if isinstance(image_data, dict):
        url = str(image_data.get("url") or "").strip()
        if url:
            return _download_binary(url)
    return _download_binary(_extract_image_url(output))


def generate_product_lifestyle_shot(
    image_url: str,
    prompt: str,
    fal_api_key: str | None = None,
) -> tuple[bytes, str]:
    """Generate a professional lifestyle/product shot preserving the exact product.

    Uses fal-ai/bria/product-shot which embeds the product into a generated
    scene while keeping its appearance faithful to the reference.

    Args:
        image_url: Publicly accessible URL of the product image.
        prompt: Scene/lifestyle description (e.g. "person wearing on city street").
        fal_api_key: fal.ai API key; falls back to FAL_API_KEY env var.

    Returns:
        (image_bytes, mime_type) of the generated lifestyle shot.
    """
    key = resolve_fal_key(fal_api_key)
    output = _fal_submit_and_wait(
        model=_FAL_PRODUCT_SHOT_MODEL,
        input_payload={"image_url": image_url, "prompt": prompt},
        fal_api_key=key,
        max_retries=_IMAGE_MAX_RETRIES,
    )
    image_data = output.get("image")
    if isinstance(image_data, dict):
        url = str(image_data.get("url") or "").strip()
        if url:
            return _download_binary(url)
    return _download_binary(_extract_image_url(output))


def generate_image_background_replace(
    image_url: str,
    prompt: str,
    fal_api_key: str | None = None,
) -> tuple[bytes, str]:
    """Replace only the background of *image_url*, keeping the subject pixel-perfect.

    Uses fal-ai/bria/background/replace which segments the foreground subject
    automatically and generates a new background from *prompt*.
    The product / subject in the reference image is never altered.

    Args:
        image_url: Publicly accessible URL of the original product image.
        prompt: Scene / environment description for the new background (English).
        fal_api_key: fal.ai API key; falls back to FAL_API_KEY env var.

    Returns:
        (image_bytes, mime_type) of the composited result.
    """
    key = resolve_fal_key(fal_api_key)
    output = _fal_submit_and_wait(
        model=_FAL_BG_REPLACE_MODEL,
        input_payload={"image_url": image_url, "prompt": prompt},
        fal_api_key=key,
        max_retries=_IMAGE_MAX_RETRIES,
    )
    # BRIA returns {"image": {"url": "..."}} or sometimes {"images": [...]}
    image_data = output.get("image")
    if isinstance(image_data, dict):
        url = str(image_data.get("url") or "").strip()
        if url:
            return _download_binary(url)
    return _download_binary(_extract_image_url(output))


def generate_video(
    prompt: str,
    fal_api_key: str | None = None,
    *,
    image_url: str | None = None,
    duration_sec: int = 5,
    generate_audio: bool = True,
) -> str:
    """Kling 3.0 Pro on fal.ai: text-to-video or image-to-video (``start_image_url``)."""
    key = resolve_fal_key(fal_api_key)
    ref = (image_url or "").strip()
    p = (prompt or "").strip()
    dur = max(3, min(15, int(duration_sec or 5)))
    dur_s = str(dur)
    audio = bool(generate_audio)
    if ref:
        model = _FAL_KLING_V3_PRO_IMAGE_TO_VIDEO
        payload: dict[str, Any] = {
            "start_image_url": ref,
            "prompt": p or "Subtle natural motion, cinematic lighting, gentle camera movement.",
            "duration": dur_s,
            "generate_audio": audio,
        }
    else:
        model = _FAL_KLING_V3_PRO_TEXT_TO_VIDEO
        payload = {"prompt": p, "duration": dur_s, "generate_audio": audio}
    if not str(payload.get("prompt") or "").strip():
        raise RuntimeError("Video promptu bos; Kling 3.0 Pro icin metin gerekli.")
    output = _fal_submit_and_wait(model=model, input_payload=payload, fal_api_key=key, max_retries=12)
    for field_name in ("video_url", "url"):
        value = str(output.get(field_name) or "").strip()
        if value:
            return value
    video = output.get("video")
    if isinstance(video, dict):
        value = str(video.get("url") or "").strip()
        if value:
            return value
    raise RuntimeError("fal.ai video URL donmedi.")