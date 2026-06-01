"""usage_events tablosu

Revision ID: 007_create_usage_events
Revises: 006_create_composer_drafts

"""

from alembic import op
import sqlalchemy as sa

revision = "007_create_usage_events"
down_revision = "006_create_composer_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "usage_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("image_count", sa.Integer(), nullable=True),
        sa.Column("seconds", sa.Float(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(10, 6), server_default="0", nullable=False),
        sa.Column("post_id", sa.String(length=64), nullable=True),
        sa.Column("draft_id", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_usage_events_user_id", "usage_events", ["user_id"])
    op.create_index("ix_usage_events_account_id", "usage_events", ["account_id"])
    op.create_index("ix_usage_events_timestamp", "usage_events", ["timestamp"])
    op.create_index("ix_usage_events_post_id", "usage_events", ["post_id"])
    op.create_index("ix_usage_events_draft_id", "usage_events", ["draft_id"])


def downgrade() -> None:
    op.drop_index("ix_usage_events_draft_id", table_name="usage_events")
    op.drop_index("ix_usage_events_post_id", table_name="usage_events")
    op.drop_index("ix_usage_events_timestamp", table_name="usage_events")
    op.drop_index("ix_usage_events_account_id", table_name="usage_events")
    op.drop_index("ix_usage_events_user_id", table_name="usage_events")
    op.drop_table("usage_events")
