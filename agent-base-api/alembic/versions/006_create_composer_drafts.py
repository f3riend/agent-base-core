"""composer_drafts tablosu

Revision ID: 006_create_composer_drafts
Revises: 005_create_scheduled_posts

"""

from alembic import op
import sqlalchemy as sa

revision = "006_create_composer_drafts"
down_revision = "005_create_scheduled_posts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "composer_drafts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("account_name", sa.String(length=255), server_default="", nullable=False),
        sa.Column("date", sa.String(length=32), server_default="", nullable=False),
        sa.Column("time", sa.String(length=32), server_default="", nullable=False),
        sa.Column("prompt", sa.Text(), server_default="", nullable=False),
        sa.Column("caption", sa.Text(), server_default="", nullable=False),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("image_urls", sa.JSON(), nullable=True),
        sa.Column("snapshot_json", sa.JSON(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("composer_drafts")
