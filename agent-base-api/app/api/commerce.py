"""Commerce API — /social-media/stores ve /social-media/products CRUD.

Tüm endpoint'ler bearer auth bekler (get_current_user). Ownership filtre
service katmanında uygulanır.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.schemas.commerce import (
    ProductCreate,
    ProductFaqIn,
    ProductFaqRead,
    ProductListItem,
    ProductRead,
    ProductReviewIn,
    ProductReviewRead,
    ProductUpdate,
    StoreCreate,
    StoreRead,
    StoreUpdate,
)
from app.services import commerce_service


router = APIRouter(prefix="/social-media", tags=["commerce"])


# ---------------------------------------------------------------------------
# Stores
# ---------------------------------------------------------------------------


@router.get("/stores", response_model=list[StoreRead])
def list_stores(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return commerce_service.list_stores(db, user_id=int(user.id))


@router.post("/stores", response_model=StoreRead, status_code=status.HTTP_201_CREATED)
def create_store(
    body: StoreCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not (body.name or "").strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "name boş olamaz.")
    return commerce_service.create_store(db, user_id=int(user.id), data=body)


@router.patch("/stores/{store_id}", response_model=StoreRead)
def update_store(
    store_id: uuid.UUID,
    body: StoreUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    store = commerce_service.update_store(
        db, user_id=int(user.id), store_id=store_id, data=body
    )
    if store is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Mağaza bulunamadı.")
    return store


@router.delete("/stores/{store_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_store(
    store_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ok = commerce_service.delete_store(db, user_id=int(user.id), store_id=store_id)
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Mağaza bulunamadı.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------


@router.get("/products", response_model=list[ProductListItem])
def list_products(
    store_id: uuid.UUID | None = Query(None),
    category: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    q: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return commerce_service.list_products(
        db,
        user_id=int(user.id),
        store_id=store_id,
        category=category,
        status=status_filter,
        q=q,
    )


@router.get("/products/{product_id}", response_model=ProductRead)
def get_product(
    product_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    product = commerce_service.get_product(db, user_id=int(user.id), product_id=product_id)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ürün bulunamadı.")
    return product


@router.post("/products", response_model=ProductRead, status_code=status.HTTP_201_CREATED)
def create_product(
    body: ProductCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not (body.name or "").strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "name boş olamaz.")
    try:
        return commerce_service.create_product(db, user_id=int(user.id), data=body)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.patch("/products/{product_id}", response_model=ProductRead)
def update_product(
    product_id: uuid.UUID,
    body: ProductUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    product = commerce_service.update_product(
        db, user_id=int(user.id), product_id=product_id, data=body
    )
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ürün bulunamadı.")
    return product


@router.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(
    product_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ok = commerce_service.delete_product(db, user_id=int(user.id), product_id=product_id)
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ürün bulunamadı.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Nested: ürüne yorum / SSS ekle (sağ panel "Veri Ekle" formları)
# ---------------------------------------------------------------------------


@router.post(
    "/products/{product_id}/reviews",
    response_model=ProductReviewRead,
    status_code=status.HTTP_201_CREATED,
)
def add_product_review(
    product_id: uuid.UUID,
    body: ProductReviewIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    review = commerce_service.add_review(
        db, user_id=int(user.id), product_id=product_id, data=body
    )
    if review is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ürün bulunamadı.")
    return review


@router.post(
    "/products/{product_id}/faqs",
    response_model=ProductFaqRead,
    status_code=status.HTTP_201_CREATED,
)
def add_product_faq(
    product_id: uuid.UUID,
    body: ProductFaqIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    faq = commerce_service.add_faq(
        db, user_id=int(user.id), product_id=product_id, data=body
    )
    if faq is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ürün bulunamadı.")
    return faq
