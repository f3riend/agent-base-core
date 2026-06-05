"""product_price_history tablosu

Revision ID: 011_create_price_history
Revises: 010_add_product_fields
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "011_create_price_history"
down_revision = "010_add_product_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_price_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("old_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("new_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("change_reason", sa.Text(), nullable=True),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column("changed_by", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["changed_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_product_price_history_product_id", "product_price_history", ["product_id"]
    )
    op.create_index(
        "ix_product_price_history_product_changed_at",
        "product_price_history",
        ["product_id", "changed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_product_price_history_product_changed_at", table_name="product_price_history"
    )
    op.drop_index("ix_product_price_history_product_id", table_name="product_price_history")
    op.drop_table("product_price_history")
