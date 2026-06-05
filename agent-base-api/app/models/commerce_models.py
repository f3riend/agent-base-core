"""Commerce modelleri — sipariş, müşteri, stok hareketi, günlük metrik, kampanya."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class ProductPriceHistory(Base):
    __tablename__ = "product_price_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    old_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    new_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    change_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    changed_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (UniqueConstraint("store_id", "email", name="uq_customers_store_email"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_orders: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    total_spent: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), server_default="0", nullable=False
    )
    first_order_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_order_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=sa.text("'{}'::text[]"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    orders: Mapped[list["Order"]] = relationship("Order", back_populates="customer")


class Order(Base):
    __tablename__ = "orders"

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
    customer_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("customers.id", ondelete="SET NULL"), nullable=True
    )
    customer_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    customer_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, server_default="pending", nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    discount_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), server_default="0", nullable=False
    )
    shipping_cost: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), server_default="0", nullable=False
    )
    payment_method: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    ordered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    customer: Mapped["Customer | None"] = relationship("Customer", back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(
        "OrderItem", back_populates="order", cascade="all, delete-orphan"
    )
    stock_movements: Mapped[list["StockMovement"]] = relationship(
        "StockMovement", back_populates="order"
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="SET NULL"),
        nullable=True,
    )
    product_name: Mapped[str] = mapped_column(Text, nullable=False)
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    discount_pct: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), server_default="0", nullable=False
    )
    line_total: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)

    order: Mapped["Order"] = relationship("Order", back_populates="items")


class StockMovement(Base):
    __tablename__ = "stock_movements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    movement_type: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    stock_after: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="SET NULL"), nullable=True
    )
    moved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    moved_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    order: Mapped["Order | None"] = relationship("Order", back_populates="stock_movements")


class ProductDailyMetrics(Base):
    __tablename__ = "product_daily_metrics"
    __table_args__ = (
        UniqueConstraint("product_id", "date", name="uq_product_daily_metrics_product_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    views: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    clicks: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    add_to_cart: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    purchases: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    revenue: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), server_default="0", nullable=False
    )
    conversion_rate: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), server_default="0", nullable=False
    )


class StoreDailyMetrics(Base):
    __tablename__ = "store_daily_metrics"
    __table_args__ = (
        UniqueConstraint("store_id", "date", name="uq_store_daily_metrics_store_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="CASCADE"),
        nullable=False,
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    total_orders: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    total_revenue: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), server_default="0", nullable=False
    )
    total_visitors: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    new_customers: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    returning_customers: Mapped[int] = mapped_column(
        Integer, server_default="0", nullable=False
    )
    avg_order_value: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), server_default="0", nullable=False
    )
    cancelled_orders: Mapped[int] = mapped_column(
        Integer, server_default="0", nullable=False
    )
    refunded_orders: Mapped[int] = mapped_column(
        Integer, server_default="0", nullable=False
    )


class CampaignPerformance(Base):
    __tablename__ = "campaign_performance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="CASCADE"),
        nullable=False,
    )
    campaign_name: Mapped[str] = mapped_column(Text, nullable=False)
    campaign_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    total_orders: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    total_revenue: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), server_default="0", nullable=False
    )
    total_views: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    total_clicks: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    cost: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), server_default="0", nullable=False
    )
    roi: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
