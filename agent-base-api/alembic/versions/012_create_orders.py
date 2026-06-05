"""customers + orders + order_items tabloları

Revision ID: 012_create_orders
Revises: 011_create_price_history
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "012_create_orders"
down_revision = "011_create_price_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------------- customers ----------------
    op.create_table(
        "customers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column("total_orders", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_spent", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("first_order_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_order_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("store_id", "email", name="uq_customers_store_email"),
    )
    op.create_index("ix_customers_store_id", "customers", ["store_id"])

    # ---------------- orders ----------------
    op.create_table(
        "orders",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("customer_name", sa.Text(), nullable=True),
        sa.Column("customer_email", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("total_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("discount_amount", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("shipping_cost", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("payment_method", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "ordered_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column("shipped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_orders_store_id", "orders", ["store_id"])
    op.create_index("ix_orders_store_ordered_at", "orders", ["store_id", "ordered_at"])
    op.create_index("ix_orders_store_status", "orders", ["store_id", "status"])

    # ---------------- order_items ----------------
    op.create_table(
        "order_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("product_name", sa.Text(), nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("discount_pct", sa.Numeric(5, 2), server_default="0", nullable=False),
        sa.Column("line_total", sa.Numeric(12, 2), nullable=True),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_order_items_order_id", "order_items", ["order_id"])


def downgrade() -> None:
    op.drop_index("ix_order_items_order_id", table_name="order_items")
    op.drop_table("order_items")

    op.drop_index("ix_orders_store_status", table_name="orders")
    op.drop_index("ix_orders_store_ordered_at", table_name="orders")
    op.drop_index("ix_orders_store_id", table_name="orders")
    op.drop_table("orders")

    op.drop_index("ix_customers_store_id", table_name="customers")
    op.drop_table("customers")
