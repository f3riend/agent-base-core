"""bchat_turns: aktif entity (ürün/marka) takibi için üç kolon

Revision ID: 018
Revises: 017
Create Date: 2026-06-18

primary_entity_type:  TEXT  (örn. 'product', 'store', 'campaign')
primary_entity_id:    TEXT  (UUID veya numeric id'yi string olarak tutar)
primary_entity_label: TEXT  (insan-okunabilir ad — "Anker Soundcore P40i ...")

conversation_context() bu kolonlardan en son non-null kaydı okuyup
"bu ürün" gibi pronoun referansları çözmek için kullanır.
"""
from alembic import op
import sqlalchemy as sa


revision = '018'
down_revision = '017'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('bchat_turns', sa.Column('primary_entity_type', sa.Text(), nullable=True))
    op.add_column('bchat_turns', sa.Column('primary_entity_id', sa.Text(), nullable=True))
    op.add_column('bchat_turns', sa.Column('primary_entity_label', sa.Text(), nullable=True))
    op.create_index(
        'ix_bchat_turns_session_entity',
        'bchat_turns',
        ['session_id', 'id'],
        postgresql_where=sa.text("primary_entity_label IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index('ix_bchat_turns_session_entity', table_name='bchat_turns')
    op.drop_column('bchat_turns', 'primary_entity_label')
    op.drop_column('bchat_turns', 'primary_entity_id')
    op.drop_column('bchat_turns', 'primary_entity_type')
