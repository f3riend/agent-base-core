"""Eski sosyal medya + kampanya paylaşımlarını ve yüklenmiş medyayı temizler.

Silinen collection'lar:
  - scheduled_posts              (sosyal medya postları)
  - composer_drafts              (içerik oluştur taslakları)
  - campaign_scheduled_posts     (kampanya yayın kayıtları)

Her kaydın `imageUrl` + `imageUrls` + `snapshot.revisionMap` URL'leri ayrıca
`delete_image_from_storage` ile temizlenir (R2 veya yerel /media).

Kullanım:
  python -m scripts.cleanup_legacy_data --dry-run                     # tüm workspace'leri özetle
  python -m scripts.cleanup_legacy_data --workspace <uid> --dry-run
  python -m scripts.cleanup_legacy_data --workspace <uid>             # gerçek silme
  python -m scripts.cleanup_legacy_data --all                         # tüm workspace'lerden gerçek silme

Ortam değişkeni veya `app.core.env_settings`'den DATABASE_URL beklenir.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

# Repo kökünü PYTHONPATH'e ekle
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.models.social_document import SocialDocument
from app.services.content_service import delete_image_from_storage


TARGET_COLLECTIONS = ("scheduled_posts", "composer_drafts", "campaign_scheduled_posts")


def collect_urls(payload: dict[str, Any] | None) -> list[str]:
    if not isinstance(payload, dict):
        return []
    urls: set[str] = set()
    primary = payload.get("imageUrl") or payload.get("image_url")
    if isinstance(primary, str) and primary.strip():
        urls.add(primary.strip())
    multi = payload.get("imageUrls") or payload.get("image_urls")
    if isinstance(multi, list):
        for u in multi:
            if isinstance(u, str) and u.strip():
                urls.add(u.strip())
    snap_raw = payload.get("snapshot") or payload.get("revisionSnapshotJson")
    if isinstance(snap_raw, str):
        try:
            snap = json.loads(snap_raw)
        except Exception:
            snap = {}
    elif isinstance(snap_raw, dict):
        snap = snap_raw
    else:
        snap = {}
    rev_map = snap.get("revisionMap") if isinstance(snap, dict) else None
    if isinstance(rev_map, dict):
        for arr in rev_map.values():
            if isinstance(arr, list):
                for u in arr:
                    if isinstance(u, str) and u.strip():
                        urls.add(u.strip())
    return sorted(urls)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", help="Tek bir workspace_uid'i hedefle")
    parser.add_argument("--all", action="store_true", help="Tüm workspace'lerden temizle")
    parser.add_argument("--dry-run", action="store_true", help="Sayım yap, silme")
    args = parser.parse_args(argv)

    if not args.workspace and not args.all:
        parser.error("--workspace <uid> ya da --all belirtmelisin")

    db = SessionLocal()
    try:
        stmt = select(SocialDocument).where(SocialDocument.collection.in_(TARGET_COLLECTIONS))
        if args.workspace:
            stmt = stmt.where(SocialDocument.workspace_uid == args.workspace)
        rows = db.scalars(stmt).all()
        if not rows:
            print("Hiç kayıt bulunamadı.")
            return 0
        urls: list[str] = []
        per_col: dict[str, int] = {}
        for row in rows:
            per_col[row.collection] = per_col.get(row.collection, 0) + 1
            urls.extend(collect_urls(row.payload or {}))
        urls = sorted(set(urls))

        print(f"Hedef collection özeti ({len(rows)} kayıt):")
        for col, n in sorted(per_col.items()):
            print(f"  {col}: {n}")
        print(f"Silinecek/toplanmış benzersiz medya URL'i: {len(urls)}")

        if args.dry_run:
            print("DRY RUN: hiçbir şey silinmedi.")
            return 0

        deleted_media = 0
        for u in urls:
            try:
                delete_image_from_storage(u)
                deleted_media += 1
            except Exception as exc:
                print(f"  ! medya silinemedi {u}: {exc}", file=sys.stderr)

        del_stmt = delete(SocialDocument).where(SocialDocument.collection.in_(TARGET_COLLECTIONS))
        if args.workspace:
            del_stmt = del_stmt.where(SocialDocument.workspace_uid == args.workspace)
        result = db.execute(del_stmt)
        db.commit()
        print(f"Silinen DB kaydı: {result.rowcount}")
        print(f"Silinen medya dosyası: {deleted_media} / {len(urls)}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
