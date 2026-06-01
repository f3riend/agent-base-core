"""Sosyal medya verisi → MySQL JSON dokümanlari."""

from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import asc, desc, select
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.core.database import get_db
from app.models.social_document import SocialDocument
from app.models.user import User
from app.schemas.social_data import SocialCreateResponse, SocialPatchBody
from app.services.social_payload_urls import rewrite_legacy_media_urls_in_payload

router = APIRouter(prefix="/social-data", tags=["social-data"])

WORKSPACE_GLOBAL = "__global__"
WORKSPACE_SYSTEM = "__system__"

ALLOWED_COLLECTIONS = frozenset(
    {
        "composer_drafts",
        "labels",
        "tickets",
        "products",
        "product_reviews",
        "product_faq",
        "product_support_tickets",
        "product_metrics_daily",
        "product_assets",
        "content_templates",
        "campaign_templates",
        "campaign_accounts",
        "campaign_scheduled_posts",
        "campaign_composer_drafts",
        "accounts",
        "scheduled_posts",
        "story_scheduled_posts",
        "banner_scheduled_posts",
        "app_settings",
        "content_templates_global",
        "campaign_templates_global",
        "agents",
        "automation_rules",
        "automation_events",
        "automation_workflows",
        "stores_runtime",
    }
)


def _resolve_workspace_uid(user: User, collection: str) -> str:
    if collection in {"content_templates_global", "campaign_templates_global"}:
        return WORKSPACE_GLOBAL
    if collection == "agents":
        return WORKSPACE_SYSTEM
    return user.workspace_uid


def _order_by_clause(collection: str):
    if collection in ("labels", "tickets", "content_templates_global", "campaign_templates_global"):
        return (asc(SocialDocument.created_at),)
    if collection in (
        "accounts",
        "scheduled_posts",
        "story_scheduled_posts",
        "banner_scheduled_posts",
        "content_templates",
        "campaign_templates",
        "campaign_accounts",
        "campaign_scheduled_posts",
        "campaign_composer_drafts",
    ):
        return (desc(SocialDocument.created_at),)
    return (desc(SocialDocument.updated_at),)


def _require_collection(collection: str) -> None:
    if collection not in ALLOWED_COLLECTIONS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Gecersiz koleksiyon.")


def _shallow_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    out.update(patch)
    return out


def _apply_merge_unset(payload: dict[str, Any], merge: dict[str, Any], unset: list[str]) -> dict[str, Any]:
    out = copy.deepcopy(payload) if payload else {}
    for k in unset:
        out.pop(k, None)
    for k, v in merge.items():
        out[k] = v
    return out


def _primary_image_url(payload: dict[str, Any]) -> str:
    image_url = str(payload.get("imageUrl") or payload.get("image_url") or "").strip()
    if image_url:
        return image_url
    image_urls = payload.get("imageUrls") if isinstance(payload.get("imageUrls"), list) else []
    if isinstance(image_urls, list):
        for raw in image_urls:
            val = str(raw or "").strip()
            if val:
                return val
    asset = payload.get("asset")
    if isinstance(asset, dict):
        return str(asset.get("url") or "").strip()
    return ""


def _scheduled_post_issues(doc_id: str, payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if not str(doc_id or "").strip():
        issues.append("empty_doc_id")
    explicit_id = payload.get("id")
    if explicit_id is not None and not str(explicit_id or "").strip():
        issues.append("empty_id")
    caption = str(payload.get("caption") or "").strip()
    if not caption:
        issues.append("empty_caption")
    image_url = _primary_image_url(payload)
    if not image_url:
        issues.append("empty_image")
    has_schedule = bool(
        str(payload.get("scheduledAt") or payload.get("scheduled_at") or "").strip()
        or str(payload.get("scheduledDate") or payload.get("date") or "").strip()
    )
    if not has_schedule:
        issues.append("invalid_schedule")
    approval_status = str(
        payload.get("approvalStatus")
        or payload.get("approvalState")
        or payload.get("approval_state")
        or ""
    ).strip().lower()
    if approval_status == "approved" and ("empty_caption" in issues or "empty_image" in issues):
        issues.append("broken_auto_approved")
    return issues


def _validate_collection_payload(collection: str, doc_id: str, payload: dict[str, Any]) -> None:
    if collection not in {"scheduled_posts", "campaign_scheduled_posts"}:
        return
    issues = _scheduled_post_issues(doc_id, payload)
    if issues:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"error": "invalid_scheduled_post", "issues": issues},
        )


@router.get("/collections/{collection}")
def list_documents(
    collection: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    _require_collection(collection)
    ws = _resolve_workspace_uid(user, collection)
    order = _order_by_clause(collection)
    rows = db.scalars(
        select(SocialDocument)
        .where(SocialDocument.workspace_uid == ws, SocialDocument.collection == collection)
        .order_by(*order),
    ).all()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = {"id": row.doc_id, **(row.payload or {})}
        out.append(rewrite_legacy_media_urls_in_payload(item))
    return out


@router.post("/collections/{collection}", response_model=SocialCreateResponse)
def create_document(
    collection: str,
    body: dict[str, Any],
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SocialCreateResponse:
    _require_collection(collection)
    if collection in ("content_templates_global", "campaign_templates_global", "agents"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Bu koleksiyona dogrudan yazma kapali.")
    ws = _resolve_workspace_uid(user, collection)
    doc_id = str(uuid.uuid4())
    payload = dict(body)
    now_iso = datetime.now(timezone.utc).isoformat()
    if "createdAt" not in payload and "created_at" not in payload:
        payload["createdAt"] = now_iso
    _validate_collection_payload(collection, doc_id, payload)
    row = SocialDocument(workspace_uid=ws, collection=collection, doc_id=doc_id, payload=payload)
    db.add(row)
    db.commit()
    return SocialCreateResponse(id=doc_id, payload=rewrite_legacy_media_urls_in_payload(payload))


@router.put("/collections/{collection}/{doc_id}")
def put_document(
    collection: str,
    doc_id: str,
    body: dict[str, Any],
    merge: bool = Query(False),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    _require_collection(collection)
    if collection in ("content_templates_global", "campaign_templates_global", "agents"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Bu koleksiyona dogrudan yazma kapali.")
    ws = _resolve_workspace_uid(user, collection)
    row = db.scalar(
        select(SocialDocument).where(
            SocialDocument.workspace_uid == ws,
            SocialDocument.collection == collection,
            SocialDocument.doc_id == doc_id,
        ),
    )
    if row is None:
        payload = dict(body)
        _validate_collection_payload(collection, doc_id, payload)
        row = SocialDocument(workspace_uid=ws, collection=collection, doc_id=doc_id, payload=payload)
        db.add(row)
    elif merge:
        merged = _shallow_merge(row.payload or {}, body)
        _validate_collection_payload(collection, doc_id, merged)
        row.payload = merged
    else:
        payload = dict(body)
        _validate_collection_payload(collection, doc_id, payload)
        row.payload = payload
    db.commit()
    db.refresh(row)
    return rewrite_legacy_media_urls_in_payload({"id": row.doc_id, **(row.payload or {})})


@router.patch("/collections/{collection}/{doc_id}")
def patch_document(
    collection: str,
    doc_id: str,
    body: SocialPatchBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    _require_collection(collection)
    if collection in ("content_templates_global", "campaign_templates_global", "agents"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Bu koleksiyona dogrudan yazma kapali.")
    ws = _resolve_workspace_uid(user, collection)
    row = db.scalar(
        select(SocialDocument).where(
            SocialDocument.workspace_uid == ws,
            SocialDocument.collection == collection,
            SocialDocument.doc_id == doc_id,
        ),
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Belge yok.")
    merged = _apply_merge_unset(row.payload or {}, body.merge, body.unset)
    _validate_collection_payload(collection, doc_id, merged)
    row.payload = merged
    db.commit()
    db.refresh(row)
    return rewrite_legacy_media_urls_in_payload({"id": row.doc_id, **(row.payload or {})})


@router.delete("/collections/{collection}/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    collection: str,
    doc_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    _require_collection(collection)
    if collection in ("content_templates_global", "campaign_templates_global", "agents"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Bu koleksiyona silme kapali.")
    ws = _resolve_workspace_uid(user, collection)
    row = db.scalar(
        select(SocialDocument).where(
            SocialDocument.workspace_uid == ws,
            SocialDocument.collection == collection,
            SocialDocument.doc_id == doc_id,
        ),
    )
    if row is None:
        return
    db.delete(row)
    db.commit()


@router.post("/collections/scheduled_posts/{doc_id}/claim-publish")
def claim_scheduled_post_publish(
    doc_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, bool]:
    ws = user.workspace_uid
    row = db.scalar(
        select(SocialDocument)
        .where(
            SocialDocument.workspace_uid == ws,
            SocialDocument.collection == "scheduled_posts",
            SocialDocument.doc_id == doc_id,
        )
        .with_for_update(),
    )
    if row is None:
        return {"claimed": False}
    payload = dict(row.payload or {})
    st = str(payload.get("publishStatus", "pending"))
    if st in ("published", "publishing", "failed"):
        return {"claimed": False}
    payload["publishStatus"] = "publishing"
    payload["publishStartedAt"] = datetime.now(timezone.utc).isoformat()
    payload["lastPublishError"] = ""
    row.payload = payload
    db.commit()
    return {"claimed": True}


@router.post("/admin/cleanup")
def admin_cleanup_documents(
    body: dict[str, Any],
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    action = str(body.get("action") or "cleanup_invalid").strip().lower()
    dry_run = bool(body.get("dry_run") if "dry_run" in body else True)
    ws = user.workspace_uid

    def _delete_rows(rows: list[SocialDocument]) -> int:
        if dry_run:
            return 0
        for row in rows:
            db.delete(row)
        db.commit()
        return len(rows)

    if action == "clear_scheduled_posts":
        rows = db.scalars(
            select(SocialDocument).where(
                SocialDocument.workspace_uid == ws,
                SocialDocument.collection == "scheduled_posts",
            )
        ).all()
        deleted = _delete_rows(rows)
        return {
            "action": action,
            "dry_run": dry_run,
            "inspected": len(rows),
            "deleted": deleted,
        }

    if action == "clear_drafts":
        rows = db.scalars(
            select(SocialDocument).where(
                SocialDocument.workspace_uid == ws,
                SocialDocument.collection == "composer_drafts",
            )
        ).all()
        deleted = _delete_rows(rows)
        return {
            "action": action,
            "dry_run": dry_run,
            "inspected": len(rows),
            "deleted": deleted,
        }

    if action not in {"cleanup_invalid", "clear_all"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Gecersiz cleanup action.")

    invalid_rows: list[SocialDocument] = []
    invalid_details: list[dict[str, Any]] = []
    rows = db.scalars(
        select(SocialDocument).where(
            SocialDocument.workspace_uid == ws,
            SocialDocument.collection == "scheduled_posts",
        )
    ).all()
    for row in rows:
        issues = _scheduled_post_issues(row.doc_id, dict(row.payload or {}))
        if not issues:
            continue
        invalid_rows.append(row)
        if len(invalid_details) < 100:
            invalid_details.append({"doc_id": row.doc_id, "issues": issues})

    deleted_invalid = _delete_rows(invalid_rows)
    deleted_drafts = 0
    draft_count = 0
    if action == "clear_all":
        draft_rows = db.scalars(
            select(SocialDocument).where(
                SocialDocument.workspace_uid == ws,
                SocialDocument.collection == "composer_drafts",
            )
        ).all()
        draft_count = len(draft_rows)
        deleted_drafts = _delete_rows(draft_rows)

    return {
        "action": action,
        "dry_run": dry_run,
        "inspected_scheduled_posts": len(rows),
        "invalid_found": len(invalid_rows),
        "deleted_invalid_scheduled_posts": deleted_invalid,
        "inspected_drafts": draft_count,
        "deleted_drafts": deleted_drafts,
        "invalid_samples": invalid_details,
    }
