"""Yerel disk medya depolama (MEDIA_STORAGE=local).

Ortam:
  MEDIA_STORAGE=local|r2         (varsayılan: r2 — Cloudflare R2)
  MEDIA_ROOT                     (varsayılan: <api kökü>/data/media)
  MEDIA_PUBLIC_BASE_URL          (örn. http://localhost:8080 — boşsa yalnız /media/... göreli URL)

Docker'da nginx /media/ altında aynı dizini sunar; yerel `make run` için FastAPI StaticFiles /media mount edilir.
"""

from __future__ import annotations

import os
from pathlib import Path

from loguru import logger

_logger = logger.bind(module="local-media-storage")


def use_local_media_storage() -> bool:
    return os.getenv("MEDIA_STORAGE", "r2").strip().lower() == "local"


def get_media_root() -> str:
    explicit = (os.getenv("MEDIA_ROOT") or "").strip()
    if explicit:
        return str(Path(explicit).resolve())
    here = Path(__file__).resolve()
    api_root = here.parent.parent.parent
    return str((api_root / "data" / "media").resolve())


def get_public_media_base() -> str:
    """Dış dünyaya verilecek URL öneki; sonda / yok."""
    return (os.getenv("MEDIA_PUBLIC_BASE_URL") or "").strip().rstrip("/")


def build_public_media_url(rel_path: str) -> str:
    rel = rel_path.lstrip("/").replace("\\", "/")
    path = f"/media/{rel}"
    base = get_public_media_base()
    if base:
        return f"{base}{path}"
    return path


def save_local_media_bytes(rel_path: str, data: bytes, content_type: str) -> str:
    del content_type  # metadata için ileride kullanılabilir
    root = Path(get_media_root()).resolve()
    rel = rel_path.lstrip("/").replace("\\", "/")
    if ".." in rel.split("/"):
        raise ValueError("Invalid media relative path.")
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("Media path escapes MEDIA_ROOT.") from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    url = build_public_media_url(rel)
    _logger.debug("Yerel medya yazildi bytes={} url={}", len(data), url[:120])
    return url


def try_delete_local_media_by_url(url: str) -> bool:
    """URL bizim /media/ yapısındaysa dosyayı sil; başarı True."""
    u = (url or "").strip()
    marker = "/media/"
    if marker not in u:
        return False
    rel = u.split(marker, 1)[1].split("?", 1)[0].strip()
    if not rel or ".." in rel.split("/"):
        _logger.debug("Yerel silme atlandi (gecersiz path): {}", u[:160])
        return False
    root = Path(get_media_root()).resolve()
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        _logger.debug("Yerel silme atlandi (path traversal): {}", u[:160])
        return False
    if target.is_file():
        try:
            target.unlink()
            _logger.info("Yerel medya silindi: {}", rel)
        except OSError as exc:
            raise RuntimeError(f"Yerel medya silinemedi: {rel}: {exc}") from exc
        return True
    _logger.debug("Yerel silme atlandi (dosya yok): {}", rel)
    return True
