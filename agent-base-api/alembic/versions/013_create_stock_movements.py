"""stock_movements tablosu

Revision ID: 013_create_stock_movements
Revises: 012_create_orders
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "013_create_stock_movements"
down_revision = "012_create_orders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stock_movements",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("movement_type", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("stock_after", sa.Integer(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "moved_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column("moved_by", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["moved_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_stock_movements_product_id", "stock_movements", ["product_id"])
    op.create_index(
        "ix_stock_movements_product_moved_at",
        "stock_movements",
        ["product_id", "moved_at"],
    )
    op.create_index(
        "ix_stock_movements_product_type",
        "stock_movements",
        ["product_id", "movement_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_stock_movements_product_type", table_name="stock_movements")
    op.drop_index("ix_stock_movements_product_moved_at", table_name="stock_movements")
    op.drop_index("ix_stock_movements_product_id", table_name="stock_movements")
    op.drop_table("stock_movements")
