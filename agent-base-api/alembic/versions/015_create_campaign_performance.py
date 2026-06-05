"""campaign_performance tablosu

Revision ID: 015_create_campaign_performance
Revises: 014_create_daily_metrics
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "015_create_campaign_performance"
down_revision = "014_create_daily_metrics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "campaign_performance",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("campaign_name", sa.Text(), nullable=False),
        sa.Column("campaign_type", sa.Text(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("total_orders", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_revenue", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("total_views", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_clicks", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cost", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("roi", sa.Numeric(8, 4), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_campaign_performance_store_dates",
        "campaign_performance",
        ["store_id", "start_date", "end_date"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_campaign_performance_store_dates", table_name="campaign_performance"
    )
    op.drop_table("campaign_performance")
