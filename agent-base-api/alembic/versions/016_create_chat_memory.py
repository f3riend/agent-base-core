"""business chat memory tabloları: bchat_sessions + bchat_turns + user_memory

Revision ID: 016_create_chat_memory
Revises: 015_create_campaign_performance

NOT: Mevcut chat_sessions/chat_messages tablolarına dokunmaz (farklı amaçla
kullanılıyor). Business chat için ayrı namespace: bchat_*.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "016_create_chat_memory"
down_revision = "015_create_campaign_performance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bchat_sessions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "opened_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column("last_turn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "meta",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_bchat_sessions_user_last_turn",
        "bchat_sessions",
        ["user_id", "last_turn_at"],
    )

    op.create_table(
        "bchat_turns",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("question", sa.Text(), nullable=True),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("intent", sa.Text(), nullable=True),
        sa.Column("model_used", sa.Text(), nullable=True),
        sa.Column("tokens_used", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["bchat_sessions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_bchat_turns_session", "bchat_turns", ["session_id", "id"]
    )

    op.create_table(
        "user_memory",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("memory_key", sa.Text(), nullable=False),
        sa.Column("memory_value", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), server_default="auto", nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "memory_key", name="uq_user_memory_user_key"),
    )
    op.create_index("ix_user_memory_user_id", "user_memory", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_memory_user_id", table_name="user_memory")
    op.drop_table("user_memory")

    op.drop_index("ix_bchat_turns_session", table_name="bchat_turns")
    op.drop_table("bchat_turns")

    op.drop_index("ix_bchat_sessions_user_last_turn", table_name="bchat_sessions")
    op.drop_table("bchat_sessions")
