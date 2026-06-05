"""Commerce CRUD service — Store + Product (+ nested children).

Tüm sorgular kullanıcı ownership'ine göre filtrelenir. Yetkisiz erişim
None döner; route layer 404 çevirir.
"""
from __future__ import annotations

import uuid
from typing import Iterable

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models.product import Product
from app.models.product_faq import ProductFaq
from app.models.product_image import ProductImage
from app.models.product_review import ProductReview
from app.models.store import Store
from app.schemas.commerce import (
    ProductCreate,
    ProductFaqIn,
    ProductImageIn,
    ProductReviewIn,
    ProductUpdate,
    StoreCreate,
    StoreUpdate,
)


def _check_product_ownership(db: Session, user_id: int, product_id: uuid.UUID) -> bool:
    """Hafif ownership check — product → store → user_id eşleşmesi.
    add_review/add_faq gibi nokta operasyonlarda nested-load yapmak yerine
    sadece existence check."""
    return (
        db.scalar(
            select(Product.id)
            .join(Store, Product.store_id == Store.id)
            .where(Product.id == product_id, Store.user_id == user_id)
        )
        is not None
    )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def list_stores(db: Session, user_id: int) -> list[Store]:
    return list(
        db.scalars(
            select(Store).where(Store.user_id == user_id).order_by(Store.created_at)
        ).all()
    )


def get_store(db: Session, user_id: int, store_id: uuid.UUID) -> Store | None:
    return db.scalar(
        select(Store).where(Store.id == store_id, Store.user_id == user_id)
    )


def create_store(db: Session, user_id: int, data: StoreCreate) -> Store:
    store = Store(
        user_id=user_id,
        name=data.name.strip(),
        rating=data.rating,
        logo_url=(data.logo_url or "").strip() or None,
        banner_url=(data.banner_url or "").strip() or None,
    )
    db.add(store)
    db.commit()
    db.refresh(store)
    return store


def update_store(
    db: Session, user_id: int, store_id: uuid.UUID, data: StoreUpdate
) -> Store | None:
    store = get_store(db, user_id, store_id)
    if not store:
        return None
    payload = data.model_dump(exclude_unset=True)
    for field, value in payload.items():
        setattr(store, field, value)
    db.commit()
    db.refresh(store)
    return store


def delete_store(db: Session, user_id: int, store_id: uuid.UUID) -> bool:
    store = get_store(db, user_id, store_id)
    if not store:
        return False
    db.delete(store)
    db.commit()
    return True


# ---------------------------------------------------------------------------
# Product
# ---------------------------------------------------------------------------


def list_products(
    db: Session,
    user_id: int,
    *,
    store_id: uuid.UUID | None = None,
    category: str | None = None,
    status: str | None = None,
    q: str | None = None,
) -> list[Product]:
    # Liste kartında thumb gösterimi için images eager-loaded;
    # ProductListItem.thumb_url Product.thumb_url property'sini okur.
    stmt = (
        select(Product)
        .join(Store, Product.store_id == Store.id)
        .where(Store.user_id == user_id)
        .options(selectinload(Product.images))
    )
    if store_id is not None:
        stmt = stmt.where(Product.store_id == store_id)
    if category:
        stmt = stmt.where(Product.category == category)
    if status:
        stmt = stmt.where(Product.status == status)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(Product.name.ilike(like), Product.description.ilike(like))
        )
    stmt = stmt.order_by(Product.created_at.desc())
    return list(db.scalars(stmt).all())


def get_product(
    db: Session, user_id: int, product_id: uuid.UUID
) -> Product | None:
    """Detay sorgusu — nested children eager-loaded."""
    return db.scalar(
        select(Product)
        .join(Store, Product.store_id == Store.id)
        .where(and_(Product.id == product_id, Store.user_id == user_id))
        .options(
            selectinload(Product.images),
            selectinload(Product.reviews),
            selectinload(Product.faqs),
            selectinload(Product.metrics_weekly),
        )
    )


def create_product(
    db: Session, user_id: int, data: ProductCreate
) -> Product:
    """Transactional create — product + nested children tek commit."""
    store = get_store(db, user_id, data.store_id)
    if not store:
        raise ValueError("Mağaza bulunamadı veya size ait değil.")

    product = Product(
        store_id=data.store_id,
        name=data.name.strip(),
        description=data.description,
        category=data.category,
        brand=data.brand,
        currency=(data.currency or "TRY").strip().upper(),
        status=(data.status or "active").strip().lower(),
        price=data.price,
        discount=data.discount,
        discount_type=data.discount_type,
        stock=data.stock,
        rating=data.rating,
        rating_count=data.rating_count,
    )
    db.add(product)
    db.flush()  # product.id elde et

    # Nested: images
    for idx, img in enumerate(data.images):
        if isinstance(img, ProductImageIn):
            url, order = img.url, img.sort_order
        else:
            url, order = str(img), idx
        url = (url or "").strip()
        if not url:
            continue
        db.add(ProductImage(product_id=product.id, url=url, sort_order=order))

    # Nested: reviews
    for r in data.reviews:
        db.add(
            ProductReview(
                product_id=product.id,
                rating=r.rating,
                content=r.content,
                review_date=r.review_date,
            )
        )

    # Nested: faqs
    for f in data.faqs:
        db.add(
            ProductFaq(
                product_id=product.id,
                question=f.question,
                answer=f.answer,
            )
        )

    db.commit()
    db.refresh(product)
    # Detay yanıtı için eager-load
    return get_product(db, user_id, product.id) or product


def update_product(
    db: Session, user_id: int, product_id: uuid.UUID, data: ProductUpdate
) -> Product | None:
    product = get_product(db, user_id, product_id)
    if not product:
        return None
    payload = data.model_dump(exclude_unset=True)
    for field, value in payload.items():
        setattr(product, field, value)
    db.commit()
    db.refresh(product)
    return product


def delete_product(
    db: Session, user_id: int, product_id: uuid.UUID
) -> bool:
    product = get_product(db, user_id, product_id)
    if not product:
        return False
    db.delete(product)
    db.commit()
    return True


# ---------------------------------------------------------------------------
# Nested: review / faq ekle (sağ paneldeki "Veri Ekle" formları için)
# ---------------------------------------------------------------------------


def add_review(
    db: Session, user_id: int, product_id: uuid.UUID, data: ProductReviewIn
) -> ProductReview | None:
    """Ürüne yorum ekle. Ürün bulunamaz/ownership eşleşmezse None."""
    if not _check_product_ownership(db, user_id, product_id):
        return None
    review = ProductReview(
        product_id=product_id,
        rating=data.rating,
        content=(data.content or "").strip() or None,
        review_date=(data.review_date or "").strip() or None,
    )
    db.add(review)
    db.commit()
    db.refresh(review)
    return review


def add_faq(
    db: Session, user_id: int, product_id: uuid.UUID, data: ProductFaqIn
) -> ProductFaq | None:
    """Ürüne SSS ekle. Ürün bulunamaz/ownership eşleşmezse None."""
    if not _check_product_ownership(db, user_id, product_id):
        return None
    faq = ProductFaq(
        product_id=product_id,
        question=(data.question or "").strip() or None,
        answer=(data.answer or "").strip() or None,
    )
    db.add(faq)
    db.commit()
    db.refresh(faq)
    return faq
