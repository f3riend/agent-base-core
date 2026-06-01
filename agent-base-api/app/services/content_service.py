"""Instagram-oriented content generation and publishing.

Raster images use OpenAI ``gpt-image-2`` (see ``openai_ad_pipeline.generate_ad`` /
``ai_client``). Prompts are enriched via ContentIntelligenceService and structured by
PromptBuilder — unchanged.

**Media pipelines:** Instagram feed/story publish uses letterboxed JPEG exports in this
module. Facebook Page photos use ``ensure_facebook_page_image_dimensions`` (1200×630),
separate from Instagram sizes. **Campaign banner** publishing goes through
``/social-media/campaign/publish`` to the Campaign API only — it must not call Instagram
normalize helpers here.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from io import BytesIO
import time
import uuid
from enum import Enum
from typing import Any, Final, Literal

import requests
from loguru import logger
from openai import OpenAI

from app.integrations.r2_storage import try_delete_r2_object_by_public_url, upload_r2_bytes
from app.services.local_media_storage import (
    save_local_media_bytes,
    try_delete_local_media_by_url,
    use_local_media_storage,
)
from app.integrations.ai_client import (
    generate_image_with_openai_edit,
    generate_image_with_openai_edit_multi,
    generate_video as fal_generate_video,
)
from app.services.openai_ad_pipeline import generate_ad
from app.integrations.instagram_client import (
    create_carousel_container,
    create_carousel_item_container,
    create_media_container,
    create_reel_container,
    create_story_container,
    create_story_video_container,
    post_multi_photo_to_facebook_page,
    post_photo_to_facebook_page,
    post_video_to_facebook_page,
    probe_image_url,
    probe_video_url,
    publish_media,
    validate_instagram_image_url,
    wait_for_media_container_ready,
)
from app.schemas.content import ContentContext
from app.services.content_intelligence_service import ContentIntelligenceService
from app.services.prompt_builder import PromptBuilder

_cis = ContentIntelligenceService()
_pb = PromptBuilder()
_logger = logger.bind(module="content-service")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_IMAGE_VARIANTS: Final[int] = 4
_MIN_IMAGE_VARIANTS: Final[int] = 1
_MAX_REFERENCE_BYTES: Final[int] = 20 * 1024 * 1024  # 20 MiB safety cap
_MAX_VIDEO_DOWNLOAD_BYTES: Final[int] = 120 * 1024 * 1024
_MAX_VIDEO_UPLOAD_BYTES: Final[int] = 80 * 1024 * 1024
_DOWNLOAD_TIMEOUT_S: Final[int] = 45
OPENAI_CAPTION_MODEL: Final[str] = "gpt-4o-mini"


class ImageStyle(str, Enum):
    """Visual intent presets layered into image prompts."""

    lifestyle = "lifestyle"
    studio = "studio"
    ecommerce = "ecommerce"
    instagram_ad = "instagram_ad"


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def _upload_image_bytes_to_storage_path(image_bytes: bytes, blob_path: str, content_type: str) -> str:
    """Yerel disk veya Cloudflare R2; *blob_path* mantıksal göreli yol (api_uploads/...)."""
    if use_local_media_storage():
        return save_local_media_bytes(blob_path, image_bytes, content_type)
    from app.integrations.r2_storage import is_r2_fully_configured

    if not is_r2_fully_configured():
        raise ValueError(
            "R2 yapilandirmasi eksik veya konteynerde bos override edilmis (R2_ACCOUNT_ID, "
            "R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME, R2_PUBLIC_BASE_URL / "
            "R2_PUBLIC_R2_DEV_HOST). Docker: docker-compose.yml icinde bu anahtarlari "
            "${R2_...:-} ile vermeyin; degerler agent-base-api/.env uzerinden gelsin. "
            "Yerel deneme: MEDIA_STORAGE=local."
        )
    return upload_r2_bytes(blob_path, image_bytes, content_type)


def _upload_image_bytes(image_bytes: bytes, filename: str, content_type: str) -> str:
    """Default namespace: anonymous API uploads (legacy)."""
    path = f"api_uploads/{uuid.uuid4().hex}_{filename}"
    return _upload_image_bytes_to_storage_path(image_bytes, path, content_type)


def _get_ffmpeg_executable() -> str | None:
    p = shutil.which("ffmpeg")
    if p:
        return p
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _normalize_video_bytes_for_instagram_graph(data: bytes, source_suffix: str) -> bytes:
    """Kling vb. ciktiyi Instagram Graph (Reels/Story) ile uyumlu H.264 + yuv420p + 9:16 kareye cevirir."""
    if len(data) < 256:
        return data
    ffmpeg = _get_ffmpeg_executable()
    if not ffmpeg:
        _logger.debug("ffmpeg bulunamadi; video yeniden kodlama atlandi (PATH veya imageio-ffmpeg).")
        return data
    suf = (source_suffix or ".mp4").lower()
    if not suf.startswith("."):
        suf = f".{suf}"
    in_path = out_path = None
    try:
        fd, in_path = tempfile.mkstemp(suffix=suf)
        os.close(fd)
        with open(in_path, "wb") as wf:
            wf.write(data)
        out_path = in_path + ".ig.mp4"
        vf = (
            "scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2,format=yuv420p"
        )
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            in_path,
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-profile:v",
            "main",
            "-preset",
            "fast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            out_path,
        ]
        r = subprocess.run(cmd, capture_output=True, timeout=600)
        if r.returncode != 0:
            err = (r.stderr or b"").lower()
            if b"audio" in err or b"matches no streams" in err or b"unknown encoder" in err:
                r = subprocess.run(
                    [
                        ffmpeg,
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-i",
                        in_path,
                        "-vf",
                        vf,
                        "-c:v",
                        "libx264",
                        "-profile:v",
                        "main",
                        "-preset",
                        "fast",
                        "-crf",
                        "23",
                        "-pix_fmt",
                        "yuv420p",
                        "-an",
                        "-movflags",
                        "+faststart",
                        out_path,
                    ],
                    capture_output=True,
                    timeout=600,
                )
        if r.returncode != 0:
            _logger.warning("ffmpeg yeniden kodlama basarisiz: {}", (r.stderr or b"")[:500])
            return data
        with open(out_path, "rb") as rf:
            out = rf.read()
        if len(out) < 256:
            return data
        _logger.info("Video Instagram icin ffmpeg ile normalize edildi ({} -> {} byte).", len(data), len(out))
        return out
    except Exception as exc:
        _logger.warning("ffmpeg normalize istisna: {}", exc)
        return data
    finally:
        for p in (in_path, out_path):
            if p:
                try:
                    os.unlink(p)
                except OSError:
                    pass


def generate_social_video(
    prompt: str,
    fal_api_key: str | None = None,
    *,
    image_url: str | None = None,
    duration_sec: int = 5,
    generate_audio: bool = True,
) -> str:
    """Call fal.ai video model, download bytes, upload to depolama; return public URL."""
    prompt = (prompt or "").strip()
    if len(prompt) < 2:
        raise ValueError("Video promptu çok kısa.")
    raw_url = fal_generate_video(
        prompt,
        fal_api_key=fal_api_key,
        image_url=image_url,
        duration_sec=duration_sec,
        generate_audio=generate_audio,
    )
    raw_url = (raw_url or "").strip()
    if not raw_url:
        raise RuntimeError("Video üreticisi URL döndürmedi.")
    with requests.get(raw_url, stream=True, timeout=_DOWNLOAD_TIMEOUT_S * 4) as resp:
        resp.raise_for_status()
        buf = bytearray()
        for chunk in resp.iter_content(chunk_size=256 * 1024):
            if not chunk:
                continue
            buf.extend(chunk)
            if len(buf) > _MAX_VIDEO_DOWNLOAD_BYTES:
                raise RuntimeError("Video dosyası izin verilen boyutu aştı.")
        data = bytes(buf)
    if not data:
        raise RuntimeError("Video indirilemedi (boş gövde).")
    low = raw_url.lower().split("?", 1)[0]
    src_suf = ".webm" if low.endswith(".webm") else ".mp4"
    data = _normalize_video_bytes_for_instagram_graph(data, src_suf)
    return _upload_image_bytes(data, "clip.mp4", "video/mp4")


def ensure_video_url_for_publishing(video_url: str) -> str:
    """Instagram/Facebook için videoyu indir, Meta-uyumlu H.264 9:16 koda cevirip depoya yukle.

    FAL veya harici URL'deki ham dosya olsun: yayin oncesi ayni is akisi uygulanir.
    """
    u = (video_url or "").strip()
    if not u:
        raise ValueError("Video URL bos.")
    err = validate_instagram_image_url(u)
    if err:
        raise RuntimeError(err)
    headers = {"User-Agent": "facebookexternalhit/1.1", "Accept": "*/*"}
    with requests.get(
        u,
        stream=True,
        timeout=_DOWNLOAD_TIMEOUT_S * 4,
        headers=headers,
        allow_redirects=True,
    ) as resp:
        resp.raise_for_status()
        buf = bytearray()
        for chunk in resp.iter_content(chunk_size=256 * 1024):
            if not chunk:
                continue
            buf.extend(chunk)
            if len(buf) > _MAX_VIDEO_DOWNLOAD_BYTES:
                raise RuntimeError("Video dosyasi izin verilen boyutu asti (yeniden yukleme).")
        data = bytes(buf)
    if not data:
        raise RuntimeError("Video indirilemedi (bos govde).")
    path_low = u.split("?", 1)[0].lower()
    src_suf = ".webm" if path_low.endswith(".webm") else ".mp4"
    data = _normalize_video_bytes_for_instagram_graph(data, src_suf)
    return _upload_image_bytes(data, "clip.mp4", "video/mp4")


def delete_image_from_storage(image_url: str) -> None:
    """Delete stored media: yerel /media/... veya R2 public URL.

    Raises:
        RuntimeError: if the URL is malformed or the delete API call fails.
    """
    url = (image_url or "").strip()
    if not url:
        return
    if "/media/" in url:
        try_delete_local_media_by_url(url)
        return
    if try_delete_r2_object_by_public_url(url):
        return
    _logger.debug("Storage silme atlandi: taninmayan URL — {}", url[:120])


def _guess_content_type(filename: str) -> str:
    low = (filename or "").lower()
    if low.endswith(".mp4"):
        return "video/mp4"
    if low.endswith(".webm"):
        return "video/webm"
    if low.endswith(".mov"):
        return "video/quicktime"
    if low.endswith(".png"):
        return "image/png"
    if low.endswith(".gif"):
        return "image/gif"
    if low.endswith(".webp"):
        return "image/webp"
    return "image/jpeg"


def upload_image_bytes_to_storage(
    image_bytes: bytes,
    filename: str = "upload.jpg",
    *,
    storage_scope: str = "default",
    owner_uid: str | None = None,
) -> str:
    """Validate and store image bytes; returns a public download URL.

    *storage_scope*:
    - ``default`` — ``api_uploads/{uuid}_{filename}`` (mevcut davranış).
    - ``user_template`` — ``user_templates/{owner_uid}/{uuid}_{filename}`` (kullanıcı şablonları;
      *owner_uid* URL-safe kullanici kimligi olmali; uretimde sunucu tarafinda dogrulama onerilir).
    """
    import re as _re

    if not image_bytes:
        raise ValueError("Empty file cannot be uploaded.")
    safe_name = "".join(c for c in (filename or "upload.jpg") if c.isalnum() or c in "._-")[:180] or "upload.jpg"
    content_type = _guess_content_type(safe_name)
    max_bytes = _MAX_VIDEO_UPLOAD_BYTES if content_type.startswith("video/") else _MAX_REFERENCE_BYTES
    if len(image_bytes) > max_bytes:
        raise ValueError("File exceeds maximum allowed size.")
    if "." not in safe_name:
        safe_name = f"{safe_name}.{content_type.split('/')[-1]}"

    scope = (storage_scope or "default").strip().lower()
    uid = (owner_uid or "").strip()
    if scope == "user_template" and uid and _re.fullmatch(r"[A-Za-z0-9_-]{10,128}", uid):
        blob_path = f"user_templates/{uid}/{uuid.uuid4().hex}_{safe_name}"
        return _upload_image_bytes_to_storage_path(image_bytes, blob_path, content_type)

    return _upload_image_bytes(image_bytes, safe_name, content_type)


# ---------------------------------------------------------------------------
# Text (captions)
# ---------------------------------------------------------------------------

def _resolve_openai_key(api_key_override: str | None = None) -> str:
    key = (api_key_override or "").strip() or (os.getenv("OPENAI_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("OpenAI API key gerekli (OPENAI_API_KEY).")
    return key


_LAST_CAPTION_USAGE: dict = {}


def _last_caption_usage() -> dict:
    """Son `_generate_caption_with_openai` çağrısının usage bilgisi (input/output tokens, model)."""
    return dict(_LAST_CAPTION_USAGE)


def _generate_caption_with_openai(system_prompt: str, user_prompt: str, api_key_override: str | None = None) -> str:
    global _LAST_CAPTION_USAGE
    client = OpenAI(api_key=_resolve_openai_key(api_key_override))
    response = client.chat.completions.create(
        model=OPENAI_CAPTION_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.7,
    )
    usage = getattr(response, "usage", None)
    _LAST_CAPTION_USAGE = {
        "model": OPENAI_CAPTION_MODEL,
        "input_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
    }
    text = (response.choices[0].message.content or "").strip() if response.choices else ""
    if not text:
        raise RuntimeError("OpenAI caption cevabi bos.")
    return text


def generate_caption(
    konu: str,
    tone: str,
    openai_api_key: str | None = None,
    *,
    context: ContentContext | None = None,
    platform: str = "feed",
    reference_image_url: str | None = None,
) -> str:
    """Generate a Turkish Instagram caption for the given topic and tone.

    If *context* is provided, uses PromptBuilder for enriched output.
    If *reference_image_url* is provided without a context, calls CIS first.
    Falls back to the original simple prompt when neither is available.
    """
    konu = (konu or "").strip()
    tone = (tone or "").strip()
    if not konu:
        raise ValueError("Caption topic must not be empty.")

    # Try to resolve or build ContentContext
    ctx = context
    if ctx is None and reference_image_url:
        try:
            ctx = _cis.analyze(
                user_prompt=konu,
                reference_image_url=reference_image_url,
                platform=platform,
                openai_api_key=openai_api_key,
            )
        except Exception:
            ctx = None  # fall back gracefully

    if ctx is not None:
        # Use PromptBuilder for enriched caption
        system_prompt = (
            "Sen deneyimli bir Instagram içerik uzmanısın. "
            "Sadece istenen caption metnini üretirsin, ekstra açıklama yazmazsın."
        )
        user_prompt = _pb.build_caption_prompt(ctx, tone=tone)
        try:
            return _generate_caption_with_openai(system_prompt, user_prompt, api_key_override=openai_api_key)
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError("Caption generation failed.") from exc

    # Legacy path — no context available
    system_prompt = (
        "Sen deneyimli bir Instagram içerik uzmanısın. "
        "Sadece istenen caption metnini üretirsin, ekstra açıklama yazmazsın."
    )
    user_prompt = (
        f"Konu: {konu}\n"
        f"Ton: {tone}\n"
        "ZORUNLU CIKTI DILI: Turkce — cumleler ve hashtagler Turkce; yalnizca marka/urun adi gerekiyorsa Ingilizce kalabilir.\n"
        "Uzunluk: 2-4 cumle + 5 hashtag\n\n"
        "Kurallar:\n"
        "- Sadece caption metnini yaz, baska aciklama/ongoruler yazma.\n"
        "- Hashtagleri caption sonuna ekle.\n"
    )
    try:
        return _generate_caption_with_openai(system_prompt, user_prompt, api_key_override=openai_api_key)
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("Caption generation failed.") from exc


_HOLIDAY_CONTENT_MODEL: Final[str] = "gpt-4o"
"""Use the full GPT-4o model for holiday content — quality matters more than speed here."""

_HOLIDAY_JSON_SCHEMA_EN = {
    "caption": "string (ready-to-post Instagram caption with hashtags)",
    "image_prompt": "string (detailed English visual prompt for AI image generation, no text/lettering in image)",
}
_HOLIDAY_JSON_SCHEMA_TR = {
    "caption": "string (hazir Instagram metni, hashtaglerle)",
    "image_prompt": "string (yapay zeka gorsel uretimi icin ayrintili TURKCE gorsel tanimi; gorselde yazi/harf yok)",
}


def generate_holiday_content(
    holiday_name: str,
    date_key: str,
    locale: str = "tr",
    openai_api_key: str | None = None,
    fal_api_key: str | None = None,
    generate_image: bool = True,
    generate_video: bool = False,
    extra_instructions: str | None = None,
) -> dict[str, str]:
    """Generate rich caption (+ optional image / video) for a public holiday using GPT-4o.

    Returns ``caption``, ``image_prompt``, ``image_url``, ``video_prompt``, ``video_url``.
    """
    import json as _json

    api_key = (openai_api_key or "").strip() or (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("Özel gün içeriği için OpenAI API anahtarı gerekli.")

    is_tr = (locale or "tr").lower().startswith("tr")
    lang = "Turkish" if is_tr else "English"
    lang_guard = (
        "Write the caption only in Turkish. Do not use unnecessary English words."
        if lang == "Turkish"
        else "Write the caption only in English."
    )
    image_rules_tr = (
        "- image_prompt: Ozel gun konusuna uygun, ayrintili TURKCE gorsel tanimi yaz. "
        "Sahne, ruh hali, renkler, kompozisyon ve stil (or. modern, sinematik) acik olsun. "
        "Türkiye'ye ve bu ozel gune ozgu kulturel atmosfer, semboller veya renk paletini yansit "
        "(genel stok fotograf degil; yer baglami hissi ver). "
        "Gorselde hic yazı, harf, logo veya tipografi olmasın."
    )
    image_rules_en = (
        "- image_prompt: Detailed English visual description for AI image generation. "
        "Describe scene, mood, colors, composition, and style clearly. "
        "Do NOT include any text, lettering, logos, or typography in the scene. "
        "Aim for cinematic quality, modern composition, festive mood fitting the holiday."
    )
    video_rules = (
        "- video_prompt: A single detailed ENGLISH motion / cinematography description for a short "
        "vertical social-media clip (9:16 feel). Describe camera movement, lighting, pacing, mood, "
        "and subject action. No on-screen text, subtitles, logos, or watermarks. Keep it festive "
        "and aligned with the holiday theme."
    )

    if generate_image and generate_video:
        schema_obj = {
            "caption": "string",
            "image_prompt": ("string (Turkish visual for image AI)" if is_tr else "string (English visual for image AI)"),
            "video_prompt": "string (English motion prompt for short vertical video AI)",
        }
    elif generate_video:
        schema_obj = {"caption": "string", "video_prompt": "string (English motion prompt for short vertical video AI)"}
    else:
        schema_obj = _HOLIDAY_JSON_SCHEMA_TR if is_tr else _HOLIDAY_JSON_SCHEMA_EN

    schema_block = _json.dumps(schema_obj, ensure_ascii=False, indent=2)
    rules_parts = [
        "Rules:",
        "- caption: 3-5 warm, engaging sentences + 6-10 highly relevant hashtags at the end. ",
        f"{lang_guard}",
    ]
    if generate_image:
        rules_parts.append(image_rules_tr if is_tr else image_rules_en)
    if generate_video:
        rules_parts.append(video_rules)
    system_prompt = (
        "You are an expert social media content creator. Your task is to generate high-quality "
        "Instagram content for a specific public holiday or special day.\n\n"
        "Return ONLY valid JSON — no markdown, no extra text, no code fences.\n\n"
        f"Schema:\n{schema_block}\n\n"
        + "\n".join(rules_parts)
    )

    gen_parts: list[str] = []
    if generate_image:
        gen_parts.append("image prompt")
    if generate_video:
        gen_parts.append("video motion prompt")
    user_tail = " and ".join(gen_parts) if gen_parts else "content"
    user_prompt = (
        f"Holiday / Special Day: {holiday_name}\n"
        f"Date: {date_key}\n"
        f"Caption language: {lang}\n\n"
        f"Generate the caption and {user_tail} now."
    )
    extra_raw = (extra_instructions or "").strip()
    if extra_raw:
        user_prompt += (
            "\n\nCreator extra rules (follow strictly; caption language above still applies — "
            "for image_prompt, respect visual constraints such as scripts, lettering, languages, "
            "symbols, motifs, tone, or subjects the creator wants to avoid or include):\n"
            f"{extra_raw}"
        )

    client = OpenAI(api_key=api_key)
    try:
        response = client.chat.completions.create(
            model=_HOLIDAY_CONTENT_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.7,
            max_tokens=1200,
        )
    except Exception as exc:
        raise RuntimeError(f"GPT-4o özel gün içeriği üretilemedi: {exc}") from exc

    raw = (response.choices[0].message.content or "").strip() if response.choices else ""
    if not raw:
        raise RuntimeError("GPT-4o boş yanıt döndürdü.")

    # Strip markdown fences if GPT wrapped output anyway
    if raw.startswith("```"):
        raw = raw.split("```")[-2] if raw.count("```") >= 2 else raw
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        parsed = _json.loads(raw)
    except _json.JSONDecodeError as exc:
        raise RuntimeError(f"GPT-4o JSON parse hatası: {exc} — raw: {raw[:300]}") from exc

    caption = str(parsed.get("caption") or "").strip()
    image_prompt = str(parsed.get("image_prompt") or "").strip() if generate_image else ""
    video_prompt = str(parsed.get("video_prompt") or "").strip() if generate_video else ""

    if not caption:
        raise RuntimeError("GPT-4o caption boş döndürdü.")
    if generate_image and not image_prompt:
        _logger.warning("GPT-4o image_prompt bos dondu; fallback prompt kullaniliyor.")
        if is_tr:
            image_prompt = (
                f'Kare Instagram goruntusu, modern sade stil, konu: "{holiday_name}" ({date_key}), '
                "Türkiye'ye özgü bayram/özel gün atmosferi, sıcak ve olumlu ruh hali, "
                "görselde yazı veya harf yok, yüksek kalite, sinematik kompozisyon."
            )
        else:
            image_prompt = (
                f'Square Instagram image, modern clean style, theme: "{holiday_name}" on {date_key}, '
                "festive positive mood, no text or lettering, high quality."
            )

    if generate_video and not video_prompt:
        video_prompt = (
            f"Cinematic vertical smartphone clip, festive mood, theme {holiday_name} ({date_key}), "
            "slow elegant camera moves, warm lighting, shallow depth of field, no text or logos on screen."
        )

    image_url = ""
    if generate_image:
        _logger.info("Ozel gun gorseli uretiliyor: {}", image_prompt[:120])
        try:
            images = generate_images(
                prompt=image_prompt,
                count=1,
                openai_api_key=openai_api_key,
                fal_api_key=fal_api_key,
            )
            u = images[0].get("url", "") if images else ""
            if isinstance(u, str) and u.strip():
                image_url = u.strip()
        except Exception as exc:
            _logger.warning("Ozel gun gorseli uretilemedi (devam edilecek): {}", exc)

    video_url = ""
    if generate_video:
        _logger.info("Ozel gun videosu uretiliyor: {}", video_prompt[:120])
        try:
            video_url = generate_social_video(video_prompt, fal_api_key=fal_api_key)
        except Exception as exc:
            _logger.warning("Ozel gun videosu uretilemedi (devam edilecek): {}", exc)

    return {
        "caption": caption,
        "image_prompt": image_prompt,
        "image_url": image_url,
        "video_prompt": video_prompt,
        "video_url": video_url,
    }


def refine_caption(mevcut_caption: str, revize_talebi: str, openai_api_key: str | None = None) -> str:
    """Revise an existing caption from Turkish editorial feedback."""
    mevcut_caption = (mevcut_caption or "").strip()
    revize_talebi = (revize_talebi or "").strip()
    if not mevcut_caption or not revize_talebi:
        raise ValueError("Caption and revision request must not be empty.")
    system_prompt = (
        "Sen bir Instagram editörüsün. "
        "Kullanıcı geri bildirimine göre metni revize eder, sadece yeni caption metnini döndürürsün."
    )
    user_prompt = (
        f"Mevcut caption:\n{mevcut_caption}\n\n"
        f"Revize talebi:\n{revize_talebi}\n\n"
        "ZORUNLU CIKTI DILI: Turkce — yeni caption tamamen Turkce olsun.\n"
        "Kurallar:\n"
        "- Sadece yeni captionu yaz, aciklama ekleme.\n"
        "- Ton ve uzunluk tutarli kalsin.\n"
    )
    try:
        return _generate_caption_with_openai(system_prompt, user_prompt, api_key_override=openai_api_key)
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("Caption revision failed.") from exc


# ---------------------------------------------------------------------------
# Prompt engineering (layered)
# ---------------------------------------------------------------------------


def _style_directive(style: ImageStyle) -> str:
    """Return style-specific cinematography and layout instructions."""
    if style is ImageStyle.lifestyle:
        return (
            "STYLE — LIFESTYLE:\n"
            "- Natural daylight or soft golden-hour; believable domestic or outdoor context.\n"
            "- Subject integrated into a real-feeling scene; shallow depth-of-field acceptable.\n"
            "- Avoid sterile catalog emptiness; keep environment coherent with the product.\n"
        )
    if style is ImageStyle.studio:
        return (
            "STYLE — STUDIO / CATALOG:\n"
            "- Clean seamless backdrop, controlled softbox lighting, minimal shadows.\n"
            "- Orthographic or mild three-quarter view; color-accurate, commercial-ready.\n"
        )
    if style is ImageStyle.ecommerce:
        return (
            "STYLE — ECOMMERCE PDP:\n"
            "- Hero product dominance, readable silhouette, consistent scale references.\n"
            "- Neutral or brand-aligned backdrop; no busy clutter behind SKU focal area.\n"
        )
    return (
        "STYLE — INSTAGRAM / PERFORMANCE AD:\n"
        "- Scroll-stopping composition, single clear focal story, thumb-stopping contrast.\n"
        "- Premium polish: crisp micro-contrast, controlled highlights, no muddy midtones.\n"
    )


def _global_quality_and_physics_block() -> str:
    """Shared realism, defect-avoidance, and rendering quality bar."""
    return (
        "PHYSICAL REALISM & ARTIFACT CONTROL (CRITICAL):\n"
        "- Cables/chargers: every wire must have a continuous believable path from port to plug; "
        "no floating segments, no impossible routing through solids, no duplicated connectors.\n"
        "- Liquids: coherent meniscus, plausible pour arcs, correct transparency/refraction; "
        "no mid-air blobs or viscosity violations.\n"
        "- Sale/discount badges: if present they must be planar, legible, aligned to surface "
        "perspective; no melted typography, no double-stroke ghosting, no unreadable kerning.\n"
        "- Hands/body: anatomically correct proportions; no extra fingers, fused joints, or "
        "asymmetric limbs.\n"
        "- Materials: metal reads as metal, glass as glass, fabric weave consistent with lighting.\n"
        "- Shadows/reflections must agree with light direction and contact points.\n\n"
        "GLOBAL CONSTRAINTS:\n"
        "- Unless the USER REQUEST explicitly demands on-image text or logos, do NOT add "
        "watermarks, UI chrome, random captions, or brand marks not implied by the brief.\n"
        "- No collage frames, no split-screen unless requested.\n"
        "- Photoreal or premium CGI-photoreal finish suitable for paid social.\n"
    )


def _variation_clause(variant_index: int, total: int) -> str:
    if total <= 1:
        return "VARIATION: Single definitive output."
    if variant_index == 0:
        return (
            f"VARIATION: 1/{total} — Primary interpretation: strongest match to the brief while "
            "remaining physically plausible."
        )
    return (
        f"VARIATION: {variant_index + 1}/{total} — Alternate composition: clearly different "
        "camera distance/angle/lighting setup while preserving subject identity and brief intent."
    )


def _build_direct_image_prompt(user_prompt: str, style: ImageStyle, variant_index: int, total: int) -> str:
    """Layered prompt for text-to-image (no reference frame)."""
    return (
        "ROLE: You are a senior commercial photographer and CGI art director.\n"
        "TASK: Generate ONE high-resolution marketing image from the USER REQUEST.\n\n"
        f"USER REQUEST:\n{user_prompt.strip()}\n\n"
        f"{_style_directive(style)}"
        f"{_global_quality_and_physics_block()}"
        f"{_variation_clause(variant_index, total)}\n"
    )


def _build_reference_prompt(
    user_prompt: str,
    style: ImageStyle,
    variant_index: int,
    total: int,
) -> str:
    """Structured prompt for reference-conditioned generation (strict product identity lock)."""
    return (
        "ROLE: Senior product photographer / CGI compositor.\n\n"
        "## PRIMARY DIRECTIVE — PRODUCT IDENTITY LOCK\n"
        "The REFERENCE IMAGE contains THE EXACT PRODUCT that must appear in the output.\n"
        "Copy the product pixel-perfect:\n"
        "  • Shape, silhouette, proportions — UNCHANGED\n"
        "  • All labels, logos, text, barcodes, badges — UNCHANGED\n"
        "  • Colors, gradients, material finish, reflections on the product surface — UNCHANGED\n"
        "  • Packaging seams, caps, closures, handles — UNCHANGED\n"
        "  • Scale relative to frame — UNCHANGED unless user explicitly says otherwise\n\n"
        "WHAT YOU MAY CHANGE:\n"
        "  • Background / environment / scene (lighting, studio, location, mood)\n"
        "  • Props around the product (complementary items, surfaces, textures)\n"
        "  • Atmosphere, color grading of the scene (NOT the product itself)\n\n"
        "WHAT IS STRICTLY FORBIDDEN:\n"
        "  ✗ Replacing, morphing, or redesigning the product in any way\n"
        "  ✗ Changing product colors even slightly\n"
        "  ✗ Distorting or warping the product shape or label\n"
        "  ✗ Generating a different product that 'looks similar'\n"
        "  ✗ Removing or altering any text/logo on the product\n\n"
        f"USER SCENE REQUEST:\n{user_prompt.strip()}\n\n"
        "Apply ONLY the scene/environment change described above. "
        "The product itself must be indistinguishable from the reference image.\n\n"
        f"{_style_directive(style)}"
        f"{_global_quality_and_physics_block()}"
        f"{_variation_clause(variant_index, total)}\n"
    )


def _build_revision_prompt(
    user_feedback: str,
    style: ImageStyle,
    variant_index: int,
    total: int,
) -> str:
    """Max-quality revision brief: literal fixes plus global physical consistency pass."""
    return (
        "ROLE: You are a senior commercial retoucher performing a PRODUCTION REVISION.\n"
        "INPUT: The attached image is the CURRENT MASTER. Apply USER FEEDBACK first, then run an "
        "implicit global QA pass for physical plausibility.\n\n"
        f"USER FEEDBACK (PRIMARY):\n{user_feedback.strip()}\n\n"
        "REVISION PROTOCOL:\n"
        "1) Literal compliance: implement every explicit instruction in the feedback visibly.\n"
        "2) Minimal collateral damage: do not unintentionally drift identity, palette, or SKU "
        "silhouette beyond what the feedback requires.\n"
        "3) Physical QA sweep: fix any incoherent cable routing, liquid behaviour, badge/poster "
        "warp, impossible reflections, micro-typography melting, or hand/object fusion that "
        "appears in the result — even if not mentioned, if it harms realism.\n"
        "4) Edge integrity: clean halos, no cutout fringe, consistent noise/grain vs sharpness.\n\n"
        f"{_style_directive(style)}"
        f"{_global_quality_and_physics_block()}"
        f"{_variation_clause(variant_index, total)}\n"
    )


def _build_campaign_banner_revision_prompt(
    user_feedback: str,
    variant_index: int,
    total: int,
) -> str:
    """Wide retail banner: prioritize visible headline/price/date changes; avoid template-like no-op outputs."""
    return (
        "ROLE: You are a senior digital banner retoucher for retail campaigns.\n"
        "INPUT: The primary image is the CURRENT campaign banner composition (it may already show products and copy). "
        "Additional reference images include the template layout and brand assets — use them for alignment and "
        "placement consistency, not as an excuse to ignore the user's requested text and price updates.\n\n"
        f"USER FEEDBACK (PRIMARY):\n{user_feedback.strip()}\n\n"
        "CAMPAIGN BANNER REVISION PRIORITY:\n"
        "1) Visible copy and numbers: update headline, discount %, old/new price strings, and date bands so they "
        "CLEARLY reflect what the user asked for. The result must not look like an unchanged template placeholder.\n"
        "2) Preserve the wide horizontal composition and major block layout from the primary image unless the "
        "feedback explicitly asks to rearrange them.\n"
        "3) Redraw clean, legible typography where needed; do not leave blurry or melted micro-text.\n"
        "4) Keep products and logos recognizable; avoid distorting SKU shape or brand marks.\n\n"
        f"{_style_directive(ImageStyle.studio)}"
        f"{_global_quality_and_physics_block()}"
        f"{_variation_clause(variant_index, total)}\n"
    )


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _download_image(image_url: str) -> tuple[bytes, str]:
    """Download an image URL into bytes with strict validation."""
    url = (image_url or "").strip()
    if not url:
        raise ValueError("Image URL must not be empty.")
    try:
        resp = requests.get(url, timeout=_DOWNLOAD_TIMEOUT_S, stream=True)
    except requests.RequestException as exc:
        raise RuntimeError(f"Reference image could not be downloaded: {exc}") from exc
    try:
        if resp.status_code >= 400:
            raise RuntimeError(f"Reference image URL returned HTTP {resp.status_code}.")
        content_type = (resp.headers.get("Content-Type") or "image/jpeg").split(";")[0].strip().lower()
        if not content_type.startswith("image/"):
            raise RuntimeError("URL did not return an image Content-Type.")
        chunks: list[bytes] = []
        total = 0
        for chunk in resp.iter_content(chunk_size=65536):
            if not chunk:
                continue
            total += len(chunk)
            if total > _MAX_REFERENCE_BYTES:
                raise RuntimeError("Reference image exceeds maximum allowed size.")
            chunks.append(chunk)
    finally:
        resp.close()
    data = b"".join(chunks)
    if not data:
        raise RuntimeError("Reference image body is empty.")
    return data, content_type


def _merge_reference_url_list(primary: str, extras: list[str] | None) -> list[str]:
    """Birincil URL + ek URL listesini tekrarsız, sırayı koruyarak birleştirir."""
    primary = (primary or "").strip()
    out: list[str] = []
    seen: set[str] = set()
    for u in [primary, *list(extras or [])]:
        u = (u or "").strip()
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(u)
    if not out:
        raise ValueError("En az bir referans görsel URL gerekli.")
    return out


def _clamp_variant_count(count: int | None) -> int:
    try:
        n = int(count or 1)
    except (TypeError, ValueError):
        n = 1
    return max(_MIN_IMAGE_VARIANTS, min(n, _MAX_IMAGE_VARIANTS))


_SEMANTIC_SIZES: dict[str, tuple[int, int]] = {
    "story":   (1088, 1920),  # 9:16 Instagram Hikaye
    "hikaye":  (1088, 1920),  # 9:16 Instagram Hikaye
    "reel":    (1088, 1920),  # 9:16
    "video":   (1088, 1920),  # 9:16
    "feed":    (1088, 1360),  # 4:5 Instagram Gönderi
    "post":    (1088, 1360),  # 4:5 Instagram Gönderi
    "gönderi": (1088, 1360),  # 4:5 Instagram Gönderi
}


def _parse_exact_image_size(value: str | None) -> tuple[int, int] | None:
    raw = (value or "").strip().lower().replace(" ", "")
    if not raw:
        return None
    if raw == "match_reference":
        return None
    if raw in _SEMANTIC_SIZES:
        return _SEMANTIC_SIZES[raw]
    # OpenAI image edit requires W and H divisible by 16; 700 is invalid, 704 = 44*16.
    if raw in {"campaign_banner", "banner", "1600x700", "1600x704"}:
        return (1600, 704)
    parts = raw.split("x")
    if len(parts) != 2:
        return None
    try:
        w = int(parts[0])
        h = int(parts[1])
    except ValueError:
        return None
    if w <= 0 or h <= 0 or w > 4096 or h > 4096:
        return None
    return (w, h)


def _openai_snap_dimension(n: int) -> int:
    """OpenAI ``images.edit`` / ``images.generate``: width and height must be divisible by 16."""
    n = int(n)
    if n <= 0:
        return 256
    lo = max(16, (n // 16) * 16)
    hi = min(4096, lo + 16)
    if lo == hi:
        return lo
    return hi if (n - lo) >= (hi - n) else lo


def _openai_edit_size_string(target_size: tuple[int, int] | None) -> str | None:
    """``WxH`` string valid for OpenAI image edit, or None to use client default."""
    if not target_size or len(target_size) != 2:
        return None
    w, h = int(target_size[0]), int(target_size[1])
    sw, sh = _openai_snap_dimension(w), _openai_snap_dimension(h)
    return f"{sw}x{sh}"


def _should_match_reference_size(value: str | None) -> bool:
    return (value or "").strip().lower().replace(" ", "") == "match_reference"


def _image_size_from_bytes(raw: bytes) -> tuple[int, int]:
    try:
        from PIL import Image, ImageOps

        with Image.open(BytesIO(raw)) as img:
            img = ImageOps.exif_transpose(img)
            return int(img.width), int(img.height)
    except Exception as exc:
        raise RuntimeError("Reference image size could not be detected.") from exc


def _is_campaign_banner_canvas(target_size: tuple[int, int] | None) -> bool:
    """1600x704 campaign banner — prefer cover-fit to avoid wide white letterboxing."""
    if not target_size or len(target_size) != 2:
        return False
    return int(target_size[0]) == 1600 and int(target_size[1]) == 704


def _trim_near_white_frame(im: Any, *, white_thr: int = 248) -> Any:
    """Crop uniform near-white margins (model often paints letterboxing into the bitmap)."""
    try:
        if im.mode != "RGB":
            im = im.convert("RGB")
        w, h = im.size
        if w < 32 or h < 32:
            return im
        px = im.load()
        col_n = max(8, h // 120)
        row_n = max(10, w // 150)

        def col_content(x: int) -> bool:
            n = 0
            for y in range(h):
                r, g, b = px[x, y][:3]
                if r < white_thr or g < white_thr or b < white_thr:
                    n += 1
            return n > col_n

        def row_content(y: int) -> bool:
            n = 0
            for x in range(w):
                r, g, b = px[x, y][:3]
                if r < white_thr or g < white_thr or b < white_thr:
                    n += 1
            return n > row_n

        left, right = 0, w - 1
        while left < w and not col_content(left):
            left += 1
        while right > left and not col_content(right):
            right -= 1
        top, bottom = 0, h - 1
        while top < h and not row_content(top):
            top += 1
        while bottom > top and not row_content(bottom):
            bottom -= 1
        if right <= left or bottom <= top:
            return im
        cw, ch = right - left + 1, bottom - top + 1
        # Avoid over-cropping valid wide layouts (safety).
        if cw * ch < int(w * h * 0.18):
            return im
        if cw < int(w * 0.42) or ch < int(h * 0.42):
            return im
        return im.crop((left, top, right + 1, bottom + 1))
    except Exception:
        return im


def _fit_image_bytes_to_size(
    raw: bytes,
    mime: str | None,
    target_size: tuple[int, int] | None,
    *,
    cover: bool = False,
) -> tuple[bytes, str]:
    if not target_size:
        return raw, mime or "image/png"
    try:
        from PIL import Image, ImageOps

        with Image.open(BytesIO(raw)) as img:
            rgb = img.convert("RGB")
            tw, th = int(target_size[0]), int(target_size[1])
            if cover:
                rgb = _trim_near_white_frame(rgb, white_thr=248)
                fitted = ImageOps.cover(rgb, (tw, th), method=Image.Resampling.LANCZOS)
                left = max(0, (fitted.width - tw) // 2)
                top = max(0, (fitted.height - th) // 2)
                canvas = fitted.crop((left, top, left + tw, top + th))
            else:
                resized = ImageOps.contain(rgb, target_size, method=Image.Resampling.LANCZOS)
                canvas = Image.new("RGB", target_size, (255, 255, 255))
                x = (tw - resized.width) // 2
                y = (th - resized.height) // 2
                canvas.paste(resized, (x, y))
            out = BytesIO()
            canvas.save(out, format="PNG")
            return out.getvalue(), "image/png"
    except Exception as exc:
        raise RuntimeError(f"Generated image could not be resized to {target_size[0]}x{target_size[1]}.") from exc


def _generate_and_store(
    *,
    prompt: str,
    openai_api_key: str | None,
    filename_prefix: str,
    output_size: str | None = None,
) -> dict[str, str]:
    """Text→image via ``generate_ad(..., raw_prompt_is_final=True)`` → depolama URL."""
    oai = (openai_api_key or "").strip() or (os.getenv("OPENAI_API_KEY") or "").strip()
    if not oai:
        raise RuntimeError("Görsel üretimi için OpenAI API anahtarı gerekli.")
    size_override = ""
    if output_size:
        try:
            w, h = _parse_exact_image_size(output_size)
            sw = _openai_snap_dimension(w)
            sh = _openai_snap_dimension(h)
            size_override = f"{sw}x{sh}"
        except Exception:
            size_override = ""
    try:
        out = generate_ad(
            images=None,
            user_request=prompt,
            openai_api_key=oai,
            raw_prompt_is_final=True,
            size_override=size_override or None,
        )
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("Image model request failed.") from exc
    raw = out.get("image_bytes")
    if not isinstance(raw, bytes) or not raw:
        raise RuntimeError("Image model returned empty bytes.")
    mime = str(out.get("mime") or "image/png")
    ext = "png" if "png" in mime.lower() else "jpg"
    url = _upload_image_bytes(raw, f"{filename_prefix}-{uuid.uuid4().hex[:10]}.{ext}", mime)
    return {"url": url}


def _campaign_openai_edit_bleed_suffix() -> str:
    return (
        "\n\nFill the entire output edge-to-edge at the requested pixel size. "
        "Do not leave white margins or empty strips; do not shrink the composition to a centered square; "
        "preserve the primary template's wide horizontal layout while updating only requested elements."
    )


def _generate_openai_edit_and_store(
    *,
    scene_prompt: str,
    reference_image_url: str,
    openai_api_key: str | None,
    filename_prefix: str = "gpt",
    target_size: tuple[int, int] | None = None,
) -> dict[str, str]:
    """OpenAI image edit (gpt-image-2) + depolama yuklemesi."""
    ref_bytes, _ = _download_image(reference_image_url)
    edit_size = _openai_edit_size_string(target_size)
    scene_use = scene_prompt.strip()
    if _is_campaign_banner_canvas(target_size):
        scene_use = scene_use + _campaign_openai_edit_bleed_suffix()
    try:
        raw, mime = generate_image_with_openai_edit(
            prompt=scene_use,
            reference_image_bytes=ref_bytes,
            openai_api_key=openai_api_key,
            **({"size": edit_size} if edit_size else {}),
        )
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("OpenAI image edit request failed.") from exc
    if not raw:
        raise RuntimeError("OpenAI image edit returned empty bytes.")
    raw, mime = _fit_image_bytes_to_size(raw, mime, target_size, cover=_is_campaign_banner_canvas(target_size))
    ext = "png" if "png" in (mime or "").lower() else "jpg"
    url = _upload_image_bytes(raw, f"{filename_prefix}-{uuid.uuid4().hex[:10]}.{ext}", mime)
    return {"url": url}


def _generate_openai_edit_multi_and_store(
    *,
    scene_prompt: str,
    reference_image_urls: list[str],
    openai_api_key: str | None,
    filename_prefix: str = "gpt",
    target_size: tuple[int, int] | None = None,
) -> dict[str, str]:
    """Birden fazla referans görsel ile OpenAI edit (gpt-image-2) + depolama."""
    if len(reference_image_urls) == 1:
        return _generate_openai_edit_and_store(
            scene_prompt=scene_prompt,
            reference_image_url=reference_image_urls[0],
            openai_api_key=openai_api_key,
            filename_prefix=filename_prefix,
            target_size=target_size,
        )
    image_files: list[tuple[str, bytes, str]] = []
    for i, url in enumerate(reference_image_urls):
        ref_bytes, ref_mime = _download_image(url)
        image_files.append((f"ref{i}.png", ref_bytes, ref_mime))
    multi_prompt = (
        f"{scene_prompt.strip()}\n\n"
        "Multiple reference images are provided: keep product identity and branding "
        "consistent across them when composing the final scene."
    )
    if _is_campaign_banner_canvas(target_size):
        multi_prompt = multi_prompt.strip() + _campaign_openai_edit_bleed_suffix()
    edit_size = _openai_edit_size_string(target_size)
    try:
        raw, mime = generate_image_with_openai_edit_multi(
            prompt=multi_prompt,
            image_files=image_files,
            openai_api_key=openai_api_key,
            **({"size": edit_size} if edit_size else {}),
        )
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("OpenAI multi image edit request failed.") from exc
    if not raw:
        raise RuntimeError("OpenAI image edit returned empty bytes.")
    raw, mime = _fit_image_bytes_to_size(raw, mime, target_size, cover=_is_campaign_banner_canvas(target_size))
    ext = "png" if "png" in (mime or "").lower() else "jpg"
    url = _upload_image_bytes(raw, f"{filename_prefix}-{uuid.uuid4().hex[:10]}.{ext}", mime)
    return {"url": url}


def _generate_openai_revise_and_store(
    *,
    full_prompt: str,
    reference_bytes: bytes,
    openai_api_key: str | None,
    filename_prefix: str,
    target_size: tuple[int, int] | None = None,
) -> dict[str, str]:
    edit_size = _openai_edit_size_string(target_size)
    full_use = full_prompt.strip()
    if _is_campaign_banner_canvas(target_size):
        full_use = full_use + _campaign_openai_edit_bleed_suffix()
    try:
        raw, mime = generate_image_with_openai_edit(
            prompt=full_use,
            reference_image_bytes=reference_bytes,
            openai_api_key=openai_api_key,
            **({"size": edit_size} if edit_size else {}),
        )
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("OpenAI revise image request failed.") from exc
    if not raw:
        raise RuntimeError("OpenAI image edit returned empty bytes.")
    raw, mime = _fit_image_bytes_to_size(raw, mime, target_size, cover=_is_campaign_banner_canvas(target_size))
    ext = "png" if "png" in (mime or "").lower() else "jpg"
    url = _upload_image_bytes(raw, f"{filename_prefix}-{uuid.uuid4().hex[:10]}.{ext}", mime)
    return {"url": url}


def _generate_openai_revise_multi_and_store(
    *,
    full_prompt: str,
    image_files: list[tuple[str, bytes, str]],
    openai_api_key: str | None,
    filename_prefix: str,
    target_size: tuple[int, int] | None = None,
) -> dict[str, str]:
    multi_prompt = (
        f"{full_prompt.strip()}\n\n"
        "Additional reference images are included for context (e.g. product shots); "
        "apply the revision to the primary composition while staying consistent with those references."
    )
    if _is_campaign_banner_canvas(target_size):
        multi_prompt = multi_prompt.strip() + _campaign_openai_edit_bleed_suffix()
    edit_size = _openai_edit_size_string(target_size)
    try:
        raw, mime = generate_image_with_openai_edit_multi(
            prompt=multi_prompt,
            image_files=image_files,
            openai_api_key=openai_api_key,
            **({"size": edit_size} if edit_size else {}),
        )
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("OpenAI multi-image revise request failed.") from exc
    if not raw:
        raise RuntimeError("OpenAI image edit returned empty bytes.")
    raw, mime = _fit_image_bytes_to_size(raw, mime, target_size, cover=_is_campaign_banner_canvas(target_size))
    ext = "png" if "png" in (mime or "").lower() else "jpg"
    url = _upload_image_bytes(raw, f"{filename_prefix}-{uuid.uuid4().hex[:10]}.{ext}", mime)
    return {"url": url}


# ---------------------------------------------------------------------------
# Public image APIs
# ---------------------------------------------------------------------------


def generate_images(
    prompt: str,
    count: int,
    fal_api_key: str | None = None,
    gemini_api_key: str | None = None,
    *,
    image_style: ImageStyle = ImageStyle.instagram_ad,
    context: ContentContext | None = None,
    platform: str = "feed",
    openai_api_key: str | None = None,
    use_gpt: bool = False,
    output_size: str | None = None,
) -> list[dict[str, str]]:
    """Generate text-conditioned image variants via OpenAI ``gpt-image-2`` (``generate_ad``).

    When *use_gpt* is True, skips CIS — kullanıcı promptu doğrudan görsel modele gider.
    Aksi halde ContentIntelligenceService + PromptBuilder çıktısı *olduğu gibi* kullanılır.
    """
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("Image prompt must not be empty.")
    n = _clamp_variant_count(count)
    oai_key = (openai_api_key or "").strip() or (os.getenv("OPENAI_API_KEY") or "").strip()

    if use_gpt:
        if not oai_key:
            raise RuntimeError(
                "Sadece GPT modu için OpenAI API key gerekli. Hesap ayarlarından ekleyin."
            )
        out: list[dict[str, str]] = []
        for _ in range(n):
            out.append(
                _generate_and_store(
                    prompt=prompt,
                    openai_api_key=oai_key,
                    filename_prefix="gpt",
                    output_size=output_size,
                )
            )
        return out

    if not oai_key:
        raise RuntimeError(
            "Görsel üretimi için OpenAI API anahtarı gerekli (Fal.ai kaldırıldı). "
            "Hesap ayarlarından OpenAI / GPT anahtarı ekleyin."
        )

    ctx = context
    if ctx is None:
        try:
            ctx = _cis.analyze(
                user_prompt=prompt,
                platform=platform,
                openai_api_key=oai_key,
            )
        except Exception:
            ctx = None  # fallback to legacy path

    fal_out: list[dict[str, str]] = []
    for idx in range(n):
        if ctx is not None:
            full_prompt = _pb.build_image_prompt(ctx, idx, n)
        else:
            full_prompt = _build_direct_image_prompt(prompt, image_style, idx, n)
        fal_out.append(
            _generate_and_store(
                prompt=full_prompt,
                openai_api_key=oai_key,
                filename_prefix="ai",
                output_size=output_size,
            )
        )
    return fal_out


def generate_images_from_reference(
    reference_image_url: str,
    prompt: str,
    count: int,
    fal_api_key: str | None = None,
    gemini_api_key: str | None = None,
    *,
    image_style: ImageStyle = ImageStyle.instagram_ad,
    context: ContentContext | None = None,
    platform: str = "feed",
    openai_api_key: str | None = None,
    mode: str = "background",
    reference_image_urls: list[str] | None = None,
    skip_professionalization: bool = False,
    output_size: str | None = None,
) -> list[dict[str, str]]:
    """Generate images keeping the reference subject; raster via OpenAI image edit (gpt-image-2).

    *mode* (``background`` / ``lifestyle`` / ``gpt``) yalnızca PromptBuilder + CIS ile üretilen
    metin talimatlarını etkiler; tüm modlar aynı OpenAI düzenleme uç noktasını kullanır.
    """
    _ = mode
    _ = fal_api_key
    _ = gemini_api_key
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("Prompt must not be empty.")

    merged_refs = _merge_reference_url_list(reference_image_url, reference_image_urls)
    target_size = _parse_exact_image_size(output_size)
    if _should_match_reference_size(output_size):
        ref_bytes_for_size, _ = _download_image(merged_refs[0])
        target_size = _image_size_from_bytes(ref_bytes_for_size)
    if target_size and len(target_size) == 2:
        target_size = (_openai_snap_dimension(target_size[0]), _openai_snap_dimension(target_size[1]))

    oai_key = (openai_api_key or "").strip() or (os.getenv("OPENAI_API_KEY") or "").strip()
    if not oai_key:
        raise RuntimeError(
            "Referanslı görsel üretimi için OpenAI API anahtarı gerekli. Hesap ayarlarından ekleyin."
        )

    ctx: ContentContext | None = None if skip_professionalization else context
    label_info: dict[str, object] = {}
    if ctx is None and not skip_professionalization:
        try:
            ctx = _cis.analyze(
                user_prompt=prompt,
                reference_image_url=reference_image_url,
                platform=platform,
                openai_api_key=oai_key,
            )
        except Exception:
            ctx = None

    if reference_image_url and ctx is not None and not skip_professionalization:
        try:
            label_info = _cis.extract_label_info(
                reference_image_url=reference_image_url,
                openai_api_key=oai_key,
            )
        except Exception as exc:
            _logger.warning("Etiket OCR atlandi, varsayilan prompt ile devam ediliyor: {}", exc)
            label_info = {}

    n = _clamp_variant_count(count)
    out: list[dict[str, str]] = []
    for idx in range(n):
        if ctx is not None:
            if label_info:
                scene_use = _pb.build_image_prompt_with_label(
                    context=ctx,
                    label_info=label_info,
                    variant_index=idx,
                    total=n,
                )
            else:
                scene_use = _pb.build_image_prompt(
                    context=ctx,
                    variant_index=idx,
                    total=n,
                    is_reference_mode=True,
                )
        else:
            scene_use = prompt
        out.append(
            _generate_openai_edit_multi_and_store(
                scene_prompt=scene_use,
                reference_image_urls=merged_refs,
                openai_api_key=oai_key,
                target_size=target_size,
            )
        )
    return out


def revise_image_with_feedback(
    image_url: str,
    feedback: str,
    count: int,
    fal_api_key: str | None = None,
    gemini_api_key: str | None = None,
    *,
    image_style: ImageStyle = ImageStyle.instagram_ad,
    platform: str = "feed",
    openai_api_key: str | None = None,
    reference_image_urls: list[str] | None = None,
    output_size: str | None = None,
    revision_context: Literal["social", "campaign_banner"] = "social",
) -> list[dict[str, str]]:
    """Revise an existing image from natural-language feedback.

    *revision_context* ``social`` (default): CIS analyzes the image + feedback, then
    PromptBuilder revision prompts when analysis succeeds; otherwise a legacy revision brief.

    *revision_context* ``campaign_banner``: skips CIS (no Instagram feed layout bias) and
    uses a studio-style wide-canvas revision brief.

    Optional *reference_image_urls* adds extra context images to the OpenAI edit call.
    """
    feedback = (feedback or "").strip()
    if not feedback:
        raise ValueError("Revision feedback must not be empty.")

    oai_key = (openai_api_key or "").strip() or (os.getenv("OPENAI_API_KEY") or "").strip()
    if not oai_key:
        raise RuntimeError(
            "Görsel revizyonu için OpenAI API anahtarı gerekli. Hesap ayarlarından ekleyin."
        )
    rc = (revision_context or "social").strip().lower()
    if rc not in ("social", "campaign_banner"):
        rc = "social"
    campaign_banner_native = rc == "campaign_banner"

    ctx: ContentContext | None = None
    if not campaign_banner_native:
        try:
            ctx = _cis.analyze(
                user_prompt=feedback,
                reference_image_url=image_url,
                platform=platform,
                openai_api_key=oai_key,
            )
        except Exception:
            ctx = None

    reference_bytes, reference_mime = _download_image(image_url)
    extras = [u.strip() for u in (reference_image_urls or []) if u.strip()]
    target_size = _parse_exact_image_size(output_size)
    if _should_match_reference_size(output_size):
        target_size = _image_size_from_bytes(reference_bytes)
    if target_size and len(target_size) == 2:
        target_size = (_openai_snap_dimension(target_size[0]), _openai_snap_dimension(target_size[1]))
    multi_files: list[tuple[str, bytes, str]] | None = None
    if extras:
        multi_files = [("primary.png", reference_bytes, reference_mime)]
        for j, u in enumerate(extras):
            b, m = _download_image(u)
            multi_files.append((f"context{j}.png", b, m))

    n = _clamp_variant_count(count)
    out: list[dict[str, str]] = []
    fallback_style = ImageStyle.studio if campaign_banner_native else image_style
    for idx in range(n):
        if ctx is not None:
            full_prompt = _pb.build_revision_prompt(ctx, feedback, idx, n)
        else:
            if campaign_banner_native:
                full_prompt = _build_campaign_banner_revision_prompt(feedback, idx, n)
            else:
                full_prompt = _build_revision_prompt(feedback, fallback_style, idx, n)
        if multi_files:
            out.append(
                _generate_openai_revise_multi_and_store(
                    full_prompt=full_prompt,
                    image_files=multi_files,
                    openai_api_key=oai_key,
                    filename_prefix="rev",
                    target_size=target_size,
                )
            )
        else:
            out.append(
                _generate_openai_revise_and_store(
                    full_prompt=full_prompt,
                    reference_bytes=reference_bytes,
                    openai_api_key=oai_key,
                    filename_prefix="rev",
                    target_size=target_size,
                )
            )
    return out


# ---------------------------------------------------------------------------
# Instagram publish
# ---------------------------------------------------------------------------

_FB_IG_FEED_W: Final[int] = 1080
_FB_IG_FEED_H: Final[int] = 1350
_FB_IG_STORY_W: Final[int] = 1080
_FB_IG_STORY_H: Final[int] = 1920
# Facebook Page photo / link-style share (separate from Instagram 4:5 pipeline).
_FB_PAGE_PHOTO_W: Final[int] = 1200
_FB_PAGE_PHOTO_H: Final[int] = 630


def _letterbox_rgb_to_canvas_jpeg_bytes(im, tw: int, th: int, jpeg_quality: int = 92) -> bytes:
    """Scale image to fit inside tw x th (preserve aspect), center on RGB canvas, encode JPEG."""
    from PIL import Image, ImageOps

    if getattr(im, "mode", None) == "RGBA":
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[3])
        rgb = bg
    else:
        rgb = im.convert("RGB")
    fitted = ImageOps.contain(rgb, (tw, th), method=Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (tw, th), (255, 255, 255))
    x = (tw - fitted.width) // 2
    y = (th - fitted.height) // 2
    canvas.paste(fitted, (x, y))
    buf = BytesIO()
    canvas.save(buf, format="JPEG", quality=jpeg_quality, optimize=True)
    return buf.getvalue()


def _normalize_image_url_for_instagram(url: str, tw: int, th: int, filename: str, log_label: str) -> str:
    """Download -> RGB letterbox (contain + padding) -> JPEG -> storage. No center-crop."""
    if os.getenv("INSTAGRAM_SKIP_IMAGE_ASPECT_NORMALIZE", "").strip().lower() in ("1", "true", "yes"):
        return url
    u = (url or "").strip()
    if not u.startswith("https://"):
        return u
    try:
        headers = {"User-Agent": "facebookexternalhit/1.1", "Accept": "image/*,*/*"}
        with requests.get(u, stream=True, timeout=60, headers=headers, allow_redirects=True) as resp:
            resp.raise_for_status()
            raw = resp.content
        if len(raw) > 25 * 1024 * 1024:
            _logger.warning("Instagram {} normalize atlandi (dosya cok buyuk): {} byte", log_label, len(raw))
            return u
        from PIL import Image

        im = Image.open(BytesIO(raw))
        data = _letterbox_rgb_to_canvas_jpeg_bytes(im, tw, th, jpeg_quality=92)
        if len(data) < 256:
            return u
        new_url = upload_image_bytes_to_storage(data, filename)
        _logger.info("Instagram {} gorsel {}x{} letterbox normalize edildi.", log_label, tw, th)
        return new_url
    except Exception as exc:
        _logger.warning("Instagram {} normalize atlandi: {} — {}", log_label, u[:120], exc)
        return u


def _normalize_image_url_for_facebook_page_photo(
    url: str, tw: int, th: int, filename: str, log_label: str
) -> str:
    """Facebook Page images: letterbox to link-style 1200x630 (separate from Instagram feed pipeline)."""
    if os.getenv("FACEBOOK_SKIP_PAGE_IMAGE_NORMALIZE", "").strip().lower() in ("1", "true", "yes"):
        return url
    u = (url or "").strip()
    if not u.startswith("https://"):
        return u
    try:
        headers = {"User-Agent": "facebookexternalhit/1.1", "Accept": "image/*,*/*"}
        with requests.get(u, stream=True, timeout=60, headers=headers, allow_redirects=True) as resp:
            resp.raise_for_status()
            raw = resp.content
        if len(raw) > 25 * 1024 * 1024:
            _logger.warning("Facebook {} normalize atlandi (dosya cok buyuk): {} byte", log_label, len(raw))
            return u
        from PIL import Image

        im = Image.open(BytesIO(raw))
        data = _letterbox_rgb_to_canvas_jpeg_bytes(im, tw, th, jpeg_quality=90)
        if len(data) < 256:
            return u
        new_url = upload_image_bytes_to_storage(data, filename)
        _logger.info("Facebook {} gorsel {}x{} letterbox normalize edildi.", log_label, tw, th)
        return new_url
    except Exception as exc:
        _logger.warning("Facebook {} normalize atlandi: {} — {}", log_label, u[:120], exc)
        return u


def ensure_instagram_feed_image_dimensions(image_url: str) -> str:
    """Feed tek goruntu: 4:5 (1080x1350); OAuthException 36003 (aspect ratio) riskini dusurur."""
    return _normalize_image_url_for_instagram(
        image_url, _FB_IG_FEED_W, _FB_IG_FEED_H, "ig_feed_norm.jpg", "feed"
    )


def ensure_instagram_story_photo_dimensions(image_url: str) -> str:
    """Story foto: 9:16 (1080x1920)."""
    return _normalize_image_url_for_instagram(
        image_url, _FB_IG_STORY_W, _FB_IG_STORY_H, "ig_story_norm.jpg", "story"
    )


def ensure_facebook_page_image_dimensions(image_url: str) -> str:
    """Facebook Page feed photo: 1200x630 letterbox (not Instagram 4:5)."""
    return _normalize_image_url_for_facebook_page_photo(
        image_url,
        _FB_PAGE_PHOTO_W,
        _FB_PAGE_PHOTO_H,
        "fb_page_photo_norm.jpg",
        "page_photo",
    )


def preflight_publish_image_urls_for_graph(*urls: str) -> str | None:
    """
    Fail before any Graph publish: https + image probe per URL.
    Returns an error message (Turkish) or None if all OK.
    """
    for raw in urls:
        u = (raw or "").strip()
        if not u:
            continue
        err = validate_instagram_image_url(u)
        if err:
            return err
        pe = probe_image_url(u)
        if pe:
            return pe
    return None


def post_to_instagram(
    image_url: str,
    caption: str,
    instagram_access_token: str | None = None,
    instagram_user_id: str | None = None,
) -> dict[str, Any]:
    """
    Create a media container, wait for processing, and publish to the IG user
    using a single access token + instagram user id flow.
    """
    image_url = (image_url or "").strip()
    caption = (caption or "").strip()
    if not image_url:
        raise ValueError("Image URL is required.")
    if not caption:
        raise ValueError("Caption is required.")

    err = validate_instagram_image_url(image_url)
    if err:
        raise RuntimeError(err)
    video_probe = probe_video_url(image_url)
    image_probe = probe_image_url(image_url)
    video_ok = video_probe is None
    image_ok = image_probe is None
    if not video_ok and not image_ok:
        raise RuntimeError(
            video_probe or image_probe or "URL ne gecerli video ne gecerli gorsel olarak dogrulanamadi."
        )

    token_in = (instagram_access_token or "").strip()
    if not token_in:
        raise RuntimeError("Instagram access token is required.")

    if video_ok:
        image_url = ensure_video_url_for_publishing(image_url)
    else:
        image_url = ensure_instagram_feed_image_dimensions(image_url)
        err2 = validate_instagram_image_url(image_url)
        if err2:
            raise RuntimeError(err2)
        image_probe2 = probe_image_url(image_url)
        if image_probe2 is not None:
            raise RuntimeError(image_probe2 or "Normalize sonrasi gorsel URL dogrulanamadi.")

    meta: dict[str, Any] = {}
    working_token = token_in

    share_reel_to_feed = True
    container_id = ""
    try:
        if video_ok:
            container_id = create_reel_container(
                video_url=image_url,
                caption=caption,
                access_token=working_token,
                instagram_user_id=instagram_user_id,
                share_to_feed=share_reel_to_feed,
                meta=meta,
            )
        else:
            container_id = create_media_container(
                image_url=image_url,
                caption=caption,
                access_token=working_token,
                instagram_user_id=instagram_user_id,
                meta=meta,
            )
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("Failed to create Instagram media container.") from exc

    effective_token = (meta.get("instagram_access_token") or working_token or "").strip()
    if not effective_token:
        raise RuntimeError("Instagram access token is required after container creation.")

    wait_sec = 600.0 if video_ok else 120.0
    try:
        wait_for_media_container_ready(container_id, effective_token, max_wait_sec=wait_sec)
    except RuntimeError as wait_exc:
        if (
            video_ok
            and share_reel_to_feed
            and "status_code=ERROR" in str(wait_exc)
        ):
            # Bazi hesaplarda feed+Reels birlestirmesi konteyner ERROR veriyor; yalniz Reels dene.
            share_reel_to_feed = False
            container_id = create_reel_container(
                video_url=image_url,
                caption=caption,
                access_token=effective_token,
                instagram_user_id=instagram_user_id,
                share_to_feed=False,
                meta=meta,
            )
            effective_token = (meta.get("instagram_access_token") or effective_token or "").strip()
            wait_for_media_container_ready(container_id, effective_token, max_wait_sec=wait_sec)
        else:
            raise
    except Exception as exc:
        raise RuntimeError("Instagram media container did not become ready.") from exc

    try:
        post_id = publish_media(
            container_id=container_id,
            access_token=effective_token,
            instagram_user_id=instagram_user_id,
            meta=meta,
        )
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("Instagram publish_media call failed.") from exc

    out: dict[str, Any] = {"post_id": post_id}
    if video_ok:
        out["media_kind"] = "video"
        out["instagram_format"] = "reels"
        out["reels_shared_to_feed"] = share_reel_to_feed
    else:
        out["media_kind"] = "image"
    if meta.get("instagram_access_token"):
        out["instagram_access_token"] = meta["instagram_access_token"]
    if meta.get("token_expires_in_seconds") is not None:
        out["token_expires_in_seconds"] = meta["token_expires_in_seconds"]
    return out


def post_carousel_to_instagram(
    image_urls: list[str],
    caption: str,
    instagram_access_token: str | None = None,
    instagram_user_id: str | None = None,
) -> dict[str, Any]:
    """Publish a single Instagram carousel post from multiple image URLs."""
    urls = [str(u or "").strip() for u in image_urls if str(u or "").strip()]
    if len(urls) < 2:
        raise ValueError("Carousel post icin en az 2 gorsel URL gerekli.")
    caption = (caption or "").strip()
    if not caption:
        raise ValueError("Caption is required.")
    token_in = (instagram_access_token or "").strip()
    if not token_in:
        raise RuntimeError("Instagram access token is required.")
    for url in urls:
        err = validate_instagram_image_url(url)
        if err:
            raise RuntimeError(err)
        if probe_video_url(url) is None:
            raise RuntimeError(
                "Carousel yalnizca gorseller icindir; video URL'si icin tek medya ile feed yayini kullanin."
            )
        probe_err = probe_image_url(url)
        if probe_err:
            raise RuntimeError(probe_err)
    meta: dict[str, Any] = {}
    working_token = token_in
    child_ids: list[str] = []
    normalized: list[str] = []
    for url in urls:
        nu = ensure_instagram_feed_image_dimensions(url)
        err_n = validate_instagram_image_url(nu)
        if err_n:
            raise RuntimeError(err_n)
        if probe_image_url(nu) is not None:
            raise RuntimeError(
                probe_image_url(nu) or "Carousel gorseli normalize sonrasi dogrulanamadi."
            )
        normalized.append(nu)
    for idx, url in enumerate(normalized):
        try:
            child_id = create_carousel_item_container(
                image_url=url,
                access_token=working_token,
                instagram_user_id=instagram_user_id,
                meta=meta,
            )
            child_ids.append(child_id)
            effective_token = (meta.get("instagram_access_token") or working_token or "").strip()
            if not effective_token:
                raise RuntimeError("Instagram access token is required after child creation.")
            wait_for_media_container_ready(child_id, effective_token)
            working_token = effective_token
            if idx + 1 < len(urls):
                time.sleep(1.5)
        except Exception as exc:
            raise RuntimeError(f"Carousel child ({idx + 1}/{len(urls)}) olusturulamadi: {exc}") from exc
    parent_id = create_carousel_container(
        children=child_ids,
        caption=caption,
        access_token=working_token,
        instagram_user_id=instagram_user_id,
        meta=meta,
    )
    effective_token = (meta.get("instagram_access_token") or working_token or "").strip()
    if not effective_token:
        raise RuntimeError("Instagram access token is required after carousel container creation.")
    wait_for_media_container_ready(parent_id, effective_token, max_wait_sec=300.0)
    post_id = publish_media(
        container_id=parent_id,
        access_token=effective_token,
        instagram_user_id=instagram_user_id,
        meta=meta,
    )
    out: dict[str, Any] = {"post_id": post_id, "carousel": True, "items_count": len(normalized)}
    if meta.get("instagram_access_token"):
        out["instagram_access_token"] = meta["instagram_access_token"]
    if meta.get("token_expires_in_seconds") is not None:
        out["token_expires_in_seconds"] = meta["token_expires_in_seconds"]
    return out


def post_story_to_instagram(
    image_url: str,
    instagram_access_token: str | None = None,
    instagram_user_id: str | None = None,
) -> dict[str, Any]:
    """
    Publish a single photo Story to Instagram.

    Requirements:
    - IG Business Account connected to a Facebook Page.
    - `instagram_content_publish` permission on the access token.
    - Publicly reachable https image URL (same rules as feed posts).
    """
    image_url = (image_url or "").strip()
    if not image_url:
        raise ValueError("Image URL is required.")

    err = validate_instagram_image_url(image_url)
    if err:
        raise RuntimeError(err)
    video_probe = probe_video_url(image_url)
    image_probe = probe_image_url(image_url)
    video_ok = video_probe is None
    image_ok = image_probe is None
    if not video_ok and not image_ok:
        raise RuntimeError(
            video_probe or image_probe or "URL ne gecerli video ne gecerli gorsel olarak dogrulanamadi."
        )

    token_in = (instagram_access_token or "").strip()
    if not token_in:
        raise RuntimeError("Instagram access token is required.")

    if video_ok:
        image_url = ensure_video_url_for_publishing(image_url)
    else:
        image_url = ensure_instagram_story_photo_dimensions(image_url)
        err2 = validate_instagram_image_url(image_url)
        if err2:
            raise RuntimeError(err2)
        image_probe2 = probe_image_url(image_url)
        if image_probe2 is not None:
            raise RuntimeError(image_probe2 or "Story normalize sonrasi gorsel URL dogrulanamadi.")

    meta: dict[str, Any] = {}
    working_token = token_in

    try:
        if video_ok:
            container_id = create_story_video_container(
                video_url=image_url,
                access_token=working_token,
                instagram_user_id=instagram_user_id,
                meta=meta,
            )
        else:
            container_id = create_story_container(
                image_url=image_url,
                access_token=working_token,
                instagram_user_id=instagram_user_id,
                meta=meta,
            )
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("Failed to create Instagram story container.") from exc

    effective_token = (meta.get("instagram_access_token") or working_token or "").strip()
    if not effective_token:
        raise RuntimeError("Instagram access token is required after container creation.")

    try:
        wait_for_media_container_ready(
            container_id,
            effective_token,
            max_wait_sec=600.0 if video_ok else 120.0,
        )
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("Instagram story container did not become ready.") from exc

    try:
        story_id = publish_media(
            container_id=container_id,
            access_token=effective_token,
            instagram_user_id=instagram_user_id,
            meta=meta,
        )
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("Instagram story publish call failed.") from exc

    out: dict[str, Any] = {"story_id": story_id}
    if video_ok:
        out["media_kind"] = "video"
    else:
        out["media_kind"] = "image"
    if meta.get("instagram_access_token"):
        out["instagram_access_token"] = meta["instagram_access_token"]
    if meta.get("token_expires_in_seconds") is not None:
        out["token_expires_in_seconds"] = meta["token_expires_in_seconds"]
    return out


def post_story_batch_to_instagram(
    image_urls: list[str],
    instagram_access_token: str | None = None,
    instagram_user_id: str | None = None,
) -> dict[str, Any]:
    """Publish multiple images as sequential Instagram stories."""
    urls = [str(u or "").strip() for u in image_urls if str(u or "").strip()]
    if len(urls) < 1:
        raise ValueError("Story icin en az bir medya URL gerekli.")
    if len(urls) == 1:
        return post_story_to_instagram(
            image_url=urls[0],
            instagram_access_token=instagram_access_token,
            instagram_user_id=instagram_user_id,
        )
    story_ids: list[str] = []
    latest_token: str | None = (instagram_access_token or "").strip()
    latest_exp: int | None = None
    for idx, url in enumerate(urls):
        result = post_story_to_instagram(
            image_url=url,
            instagram_access_token=latest_token,
            instagram_user_id=instagram_user_id,
        )
        sid = str(result.get("story_id") or "").strip()
        if not sid:
            raise RuntimeError(f"Story ID donmedi ({idx + 1}/{len(urls)}).")
        story_ids.append(sid)
        tok = str(result.get("instagram_access_token") or "").strip()
        if tok:
            latest_token = tok
        exp_val = result.get("token_expires_in_seconds")
        if isinstance(exp_val, int):
            latest_exp = exp_val
    out: dict[str, Any] = {"story_ids": story_ids, "items_count": len(story_ids)}
    if story_ids:
        out["story_id"] = story_ids[0]
    if latest_token:
        out["instagram_access_token"] = latest_token
    if latest_exp is not None:
        out["token_expires_in_seconds"] = latest_exp
    return out


def post_photo_to_facebook(
    image_url: str,
    caption: str,
    instagram_access_token: str | None = None,
    facebook_page_id: str | None = None,
) -> dict[str, Any]:
    """
    Publish a photo post to a Facebook Page using the Page access token
    resolved automatically from /me/accounts (same token used for Instagram).

    Args:
        image_url: Publicly reachable https image URL.
        caption: Post text / message.
        instagram_access_token: User access token (same one used for IG).
        facebook_page_id: Optional; if omitted, the first connected Page is used.
    """
    image_url = (image_url or "").strip()
    caption = (caption or "").strip()
    if not image_url:
        raise ValueError("Image URL is required.")
    if not caption:
        raise ValueError("Caption is required for Facebook post.")

    err = validate_instagram_image_url(image_url)
    if err:
        raise RuntimeError(err)

    token_in = (instagram_access_token or "").strip()
    if not token_in:
        raise RuntimeError("Instagram/Facebook access token is required.")

    if probe_video_url(image_url) is None:
        video_pub_url = ensure_video_url_for_publishing(image_url)
        return post_video_to_facebook_page(
            video_url=video_pub_url,
            caption=caption,
            access_token=token_in,
            facebook_page_id=facebook_page_id,
        )

    image_url = ensure_facebook_page_image_dimensions(image_url)
    err_fb = validate_instagram_image_url(image_url)
    if err_fb:
        raise RuntimeError(err_fb)
    if probe_image_url(image_url) is not None:
        raise RuntimeError(probe_image_url(image_url) or "Facebook gorseli normalize sonrasi dogrulanamadi.")

    return post_photo_to_facebook_page(
        image_url=image_url,
        caption=caption,
        access_token=token_in,
        facebook_page_id=facebook_page_id,
    )


def post_multi_photo_to_facebook(
    image_urls: list[str],
    caption: str,
    instagram_access_token: str | None = None,
    facebook_page_id: str | None = None,
) -> dict[str, Any]:
    """Publish multiple images as one Facebook post."""
    urls = [str(u or "").strip() for u in image_urls if str(u or "").strip()]
    if len(urls) < 2:
        raise ValueError("Coklu Facebook post icin en az 2 gorsel URL gerekli.")
    caption = (caption or "").strip()
    if not caption:
        raise ValueError("Caption is required for Facebook post.")
    for url in urls:
        err = validate_instagram_image_url(url)
        if err:
            raise RuntimeError(err)
        if probe_video_url(url) is None:
            raise RuntimeError(
                "Facebook coklu gorsel albumu yalnizca gorseller icindir; video icin tek URL ile yayinlayin."
            )
    token_in = (instagram_access_token or "").strip()
    if not token_in:
        raise RuntimeError("Instagram/Facebook access token is required.")
    normalized_fb: list[str] = []
    for url in urls:
        nu = ensure_facebook_page_image_dimensions(url)
        err_fb = validate_instagram_image_url(nu)
        if err_fb:
            raise RuntimeError(err_fb)
        if probe_image_url(nu) is not None:
            raise RuntimeError(probe_image_url(nu) or "Facebook coklu gorsel normalize sonrasi dogrulanamadi.")
        normalized_fb.append(nu)
    return post_multi_photo_to_facebook_page(
        image_urls=normalized_fb,
        caption=caption,
        access_token=token_in,
        facebook_page_id=facebook_page_id,
    )
