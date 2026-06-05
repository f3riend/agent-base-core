"""stores.banner_url kolonu

Revision ID: 009_add_store_banner_url
Revises: 008_create_commerce_and_chat

NOT: Migration zinciri şu an kırık (init_db ile tablolar oluştu).
Düzeltildiğinde bu otomatik uygulanır. O zamana kadar manuel:

    ALTER TABLE stores ADD COLUMN IF NOT EXISTS banner_url TEXT;
"""
from alembic import op
import sqlalchemy as sa


revision = "009_add_store_banner_url"
down_revision = "008_create_commerce_and_chat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("stores", sa.Column("banner_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("stores", "banner_url")
