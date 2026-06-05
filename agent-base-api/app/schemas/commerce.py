"""Pydantic şemaları — Store + Product + nested image/review/faq."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class StoreCreate(BaseModel):
    name: str
    rating: Decimal | None = None
    logo_url: str | None = None
    banner_url: str | None = None


class StoreUpdate(BaseModel):
    name: str | None = None
    rating: Decimal | None = None
    logo_url: str | None = None
    banner_url: str | None = None


class StoreRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: int
    name: str
    rating: Decimal | None
    logo_url: str | None
    banner_url: str | None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Product nested children
# ---------------------------------------------------------------------------


class ProductImageIn(BaseModel):
    url: str
    sort_order: int = 0


class ProductImageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    url: str
    sort_order: int


class ProductReviewIn(BaseModel):
    rating: int | None = None
    content: str | None = None
    review_date: str | None = None


class ProductReviewRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    rating: int | None
    content: str | None
    review_date: str | None
    created_at: datetime


class ProductFaqIn(BaseModel):
    question: str | None = None
    answer: str | None = None


class ProductFaqRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    question: str | None
    answer: str | None


class ProductMetricsWeeklyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    week_start: date | None
    sales_quantity: int | None
    revenue: Decimal | None
    order_count: int | None


# ---------------------------------------------------------------------------
# Product
# ---------------------------------------------------------------------------


class ProductCreate(BaseModel):
    """Yaratırken images URL listesi veya {url,sort_order} obje listesi olabilir."""

    store_id: uuid.UUID
    name: str
    description: str | None = None
    category: str | None = None
    brand: str | None = None
    currency: str = "TRY"
    status: str = "active"
    price: Decimal | None = None
    discount: Decimal | None = None
    discount_type: str | None = None
    stock: int | None = None
    rating: Decimal | None = None
    rating_count: int | None = None
    images: list[ProductImageIn | str] = Field(default_factory=list)
    reviews: list[ProductReviewIn] = Field(default_factory=list)
    faqs: list[ProductFaqIn] = Field(default_factory=list)


class ProductUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    category: str | None = None
    brand: str | None = None
    currency: str | None = None
    status: str | None = None
    price: Decimal | None = None
    discount: Decimal | None = None
    discount_type: str | None = None
    stock: int | None = None
    rating: Decimal | None = None
    rating_count: int | None = None
    ai_summary: str | None = None
    weekly_sales: int | None = None
    weekly_revenue: Decimal | None = None
    trend_pct: Decimal | None = None


class ProductListItem(BaseModel):
    """Liste görünümünde döner — nested children dahil değil (performans).

    thumb_url: Product.thumb_url @property'sinden gelir (ilk image URL).
    Service `selectinload(Product.images)` ile eager-load yapar.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    store_id: uuid.UUID
    name: str
    category: str | None
    brand: str | None
    status: str
    currency: str
    price: Decimal | None
    discount: Decimal | None
    discount_type: str | None
    stock: int | None
    rating: Decimal | None
    rating_count: int | None
    trend_pct: Decimal | None
    weekly_sales: int | None
    weekly_revenue: Decimal | None
    thumb_url: str | None = None


class ProductRead(BaseModel):
    """Detay görünümünde döner — images, reviews, faqs, metrics_weekly dahil."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    store_id: uuid.UUID
    name: str
    description: str | None
    category: str | None
    brand: str | None
    currency: str
    status: str
    price: Decimal | None
    discount: Decimal | None
    discount_type: str | None
    stock: int | None
    rating: Decimal | None
    rating_count: int | None
    trend_pct: Decimal | None
    trend_updated_at: datetime | None
    weekly_sales: int | None
    weekly_revenue: Decimal | None
    weekly_updated_at: datetime | None
    ai_summary: str | None
    ai_summary_updated_at: datetime | None
    created_at: datetime
    updated_at: datetime
    images: list[ProductImageRead] = Field(default_factory=list)
    reviews: list[ProductReviewRead] = Field(default_factory=list)
    faqs: list[ProductFaqRead] = Field(default_factory=list)
    metrics_weekly: list[ProductMetricsWeeklyRead] = Field(default_factory=list)
