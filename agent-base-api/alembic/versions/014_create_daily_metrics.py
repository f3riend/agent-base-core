"""product_daily_metrics + store_daily_metrics tabloları

Revision ID: 014_create_daily_metrics
Revises: 013_create_stock_movements
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "014_create_daily_metrics"
down_revision = "013_create_stock_movements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------------- product_daily_metrics ----------------
    op.create_table(
        "product_daily_metrics",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("views", sa.Integer(), server_default="0", nullable=False),
        sa.Column("clicks", sa.Integer(), server_default="0", nullable=False),
        sa.Column("add_to_cart", sa.Integer(), server_default="0", nullable=False),
        sa.Column("purchases", sa.Integer(), server_default="0", nullable=False),
        sa.Column("revenue", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("conversion_rate", sa.Numeric(5, 4), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "product_id", "date", name="uq_product_daily_metrics_product_date"
        ),
    )
    op.create_index(
        "ix_product_daily_metrics_product_date",
        "product_daily_metrics",
        ["product_id", "date"],
    )

    # ---------------- store_daily_metrics ----------------
    op.create_table(
        "store_daily_metrics",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("total_orders", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_revenue", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("total_visitors", sa.Integer(), server_default="0", nullable=False),
        sa.Column("new_customers", sa.Integer(), server_default="0", nullable=False),
        sa.Column("returning_customers", sa.Integer(), server_default="0", nullable=False),
        sa.Column("avg_order_value", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("cancelled_orders", sa.Integer(), server_default="0", nullable=False),
        sa.Column("refunded_orders", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("store_id", "date", name="uq_store_daily_metrics_store_date"),
    )
    op.create_index(
        "ix_store_daily_metrics_store_date",
        "store_daily_metrics",
        ["store_id", "date"],
    )


def downgrade() -> None:
    op.drop_index("ix_store_daily_metrics_store_date", table_name="store_daily_metrics")
    op.drop_table("store_daily_metrics")

    op.drop_index("ix_product_daily_metrics_product_date", table_name="product_daily_metrics")
    op.drop_table("product_daily_metrics")
