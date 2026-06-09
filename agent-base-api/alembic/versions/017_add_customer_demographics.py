"""add customer demographics

Revision ID: 017
Revises: 016
Create Date: 2026-06-09
"""
from alembic import op
import sqlalchemy as sa

revision = '017'
down_revision = '016_create_chat_memory'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('customers', sa.Column('city', sa.Text(), nullable=True))
    op.add_column('customers', sa.Column('gender', sa.Text(), nullable=True))
    op.add_column('customers', sa.Column('age', sa.Integer(), nullable=True))

def downgrade():
    op.drop_column('customers', 'age')
    op.drop_column('customers', 'gender')
    op.drop_column('customers', 'city')
