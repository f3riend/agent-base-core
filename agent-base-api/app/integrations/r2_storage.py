"""Cloudflare R2 (S3 uyumlu API) ile medya yükleme/silme.

Gerekli ortam (MEDIA_STORAGE r2 iken):
  R2_ACCOUNT_ID
  R2_ACCESS_KEY_ID
  R2_SECRET_ACCESS_KEY
  R2_BUCKET_NAME

Public okuma URL kökü (dönen link: ``{kök}/{object_key}``) — birini kullanın:

  R2_PUBLIC_BASE_URL
      Sonda ``/`` yok. Özel domain (ör. ``https://f4riend.win``), cloudflared hostname veya
      Worker / CDN kökü — API dönen dosya URL’leri ``{R2_PUBLIC_BASE_URL}/{object_key}`` olur.

  R2_PUBLIC_R2_DEV_HOST (isteğe bagli, ``R2_PUBLIC_BASE_URL`` bosken)
      R2 konsoldaki **Public Development URL** host’u, örnek: ``pub-c907bd83fbc04f51b4cc20aa1664fd09.r2.dev``
      (``https://`` yazmayin; kod ekler.)

MEDIA_STORAGE=local ise upload bu modülü kullanmaz; silme yalnızca yukarıdaki public kök ile
eşleşen URL'lerde R2 delete dener.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from loguru import logger

_logger = logger.bind(module="r2-storage")


def _resolve_public_base() -> str:
    base = (os.getenv("R2_PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if base:
        return base
    host = (os.getenv("R2_PUBLIC_R2_DEV_HOST") or "").strip().rstrip("/")
    if host:
        if host.startswith("http://") or host.startswith("https://"):
            return host.rstrip("/")
        return f"https://{host}"
    raise RuntimeError(
        "R2_PUBLIC_BASE_URL ayarlayin (ornek ozel domain: https://f4riend.win) veya "
        "R2_PUBLIC_R2_DEV_HOST=pub-xxxxx.r2.dev (R2 konsol Public Development URL)."
    )


def _require_public_base() -> str:
    return _resolve_public_base()


def is_r2_fully_configured() -> bool:
    """R2 put_object + public URL icin gerekli tum ortam degiskenleri dolu mu (bos override kontrolu)."""
    if not (os.getenv("R2_BUCKET_NAME") or "").strip():
        return False
    if not (
        (os.getenv("R2_ACCOUNT_ID") or "").strip()
        and (os.getenv("R2_ACCESS_KEY_ID") or "").strip()
        and (os.getenv("R2_SECRET_ACCESS_KEY") or "").strip()
    ):
        return False
    try:
        _resolve_public_base()
    except RuntimeError:
        return False
    return True


@lru_cache(maxsize=1)
def _bucket_name() -> str:
    b = (os.getenv("R2_BUCKET_NAME") or "").strip()
    if not b:
        raise RuntimeError("R2_BUCKET_NAME ayarlanmali.")
    return b


@lru_cache(maxsize=1)
def _s3_client() -> Any:
    account_id = (os.getenv("R2_ACCOUNT_ID") or "").strip()
    access_key = (os.getenv("R2_ACCESS_KEY_ID") or "").strip()
    secret_key = (os.getenv("R2_SECRET_ACCESS_KEY") or "").strip()
    if not (account_id and access_key and secret_key):
        raise RuntimeError(
            "R2_ACCOUNT_ID, R2_ACCESS_KEY_ID ve R2_SECRET_ACCESS_KEY ortam degiskenleri gerekli."
        )
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
    )


def upload_r2_bytes(key: str, data: bytes, content_type: str) -> str:
    key = key.lstrip("/").replace("\\", "/")
    if ".." in key.split("/"):
        raise ValueError("Invalid R2 object key.")
    base = _require_public_base()
    client = _s3_client()
    client.put_object(Bucket=_bucket_name(), Key=key, Body=data, ContentType=content_type)
    url = f"{base}/{key}"
    _logger.debug("R2 yuklendi key={} bytes={}", key, len(data))
    return url


def try_delete_r2_object_by_public_url(url: str) -> bool:
    """URL bilinen public kök (R2_PUBLIC_BASE_URL veya bucket.r2.dev) altindaysa objeyi sil."""
    try:
        base = _resolve_public_base()
    except RuntimeError:
        return False
    u = (url or "").strip()
    prefix = f"{base}/"
    if not u.startswith(prefix):
        return False
    key = u[len(prefix) :].split("?", 1)[0]
    if not key or ".." in key.split("/"):
        _logger.debug("R2 silme atlandi (gecersiz key): {}", u[:160])
        return False
    try:
        _s3_client().delete_object(Bucket=_bucket_name(), Key=key)
        _logger.info("R2 silindi key={}", key)
        return True
    except Exception as exc:
        em = str(exc).lower()
        if "404" in str(exc) or "not found" in em or "nosuchkey" in em:
            _logger.debug("R2 silme (zaten yok): {}", key)
            return True
        raise RuntimeError(f"R2 silme basarisiz ({key}): {exc}") from exc
