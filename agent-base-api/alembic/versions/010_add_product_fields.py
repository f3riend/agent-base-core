"""products tablosuna stok/maliyet/sku/barcode/is_active kolonları

Revision ID: 010_add_product_fields
Revises: 009_add_store_banner_url
"""
from alembic import op
import sqlalchemy as sa


revision = "010_add_product_fields"
down_revision = "009_add_store_banner_url"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("stock_quantity", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "products",
        sa.Column("stock_alert_level", sa.Integer(), server_default="5", nullable=False),
    )
    op.add_column(
        "products",
        sa.Column("cost_price", sa.Numeric(12, 2), nullable=True),
    )
    op.add_column(
        "products",
        sa.Column("sku", sa.Text(), nullable=True),
    )
    op.add_column(
        "products",
        sa.Column("barcode", sa.Text(), nullable=True),
    )
    op.add_column(
        "products",
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("products", "is_active")
    op.drop_column("products", "barcode")
    op.drop_column("products", "sku")
    op.drop_column("products", "cost_price")
    op.drop_column("products", "stock_alert_level")
    op.drop_column("products", "stock_quantity")
