"""Ürün (Product) — Store altında, görsel/yorum/SSS/metrik ilişkileriyle."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Product(Base):
    __tablename__ = "products"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )
    store_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    brand: Mapped[str | None] = mapped_column(String(128), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), server_default="TRY", nullable=False)
    status: Mapped[str] = mapped_column(String(16), server_default="active", nullable=False, index=True)
    price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    discount: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    discount_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    stock: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rating: Mapped[Decimal | None] = mapped_column(Numeric(3, 1), nullable=True)
    rating_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trend_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    trend_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    weekly_sales: Mapped[int | None] = mapped_column(Integer, nullable=True)
    weekly_revenue: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    weekly_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_summary_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    stock_quantity: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    stock_alert_level: Mapped[int] = mapped_column(Integer, server_default="5", nullable=False)
    cost_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    sku: Mapped[str | None] = mapped_column(Text, nullable=True)
    barcode: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=sa.true(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    store: Mapped["Store"] = relationship("Store", back_populates="products")
    images: Mapped[list["ProductImage"]] = relationship(
        "ProductImage", back_populates="product", cascade="all, delete-orphan",
        order_by="ProductImage.sort_order",
    )

    @property
    def thumb_url(self) -> str | None:
        """İlk image URL'i — ProductListItem.thumb_url buradan okunur.

        from_attributes=True ile Pydantic bu property'i otomatik alır.
        images eager-loaded olmalı, yoksa lazy-load N+1 riskine girer.
        """
        if self.images:
            return self.images[0].url
        return None

    reviews: Mapped[list["ProductReview"]] = relationship(
        "ProductReview", back_populates="product", cascade="all, delete-orphan",
        order_by="ProductReview.created_at.desc()",
    )
    faqs: Mapped[list["ProductFaq"]] = relationship(
        "ProductFaq", back_populates="product", cascade="all, delete-orphan",
    )
    metrics_weekly: Mapped[list["ProductMetricsWeekly"]] = relationship(
        "ProductMetricsWeekly", back_populates="product", cascade="all, delete-orphan",
        order_by="ProductMetricsWeekly.week_start.desc()",
    )
