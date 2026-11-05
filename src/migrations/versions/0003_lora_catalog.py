"""LoRA catalog and per-image generation selections.

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('loras',
        sa.Column('id', UUID(), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('filename', sa.Text(), nullable=False, unique=True),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('model', sa.Text(), server_default='z-image-turbo', nullable=False),
        sa.Column('weight', sa.Numeric(8, 4), server_default='1', nullable=False),
        sa.Column('details', JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False))
    op.create_table('image_loras',
        sa.Column('image_id', UUID(), sa.ForeignKey('images.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('lora_id', UUID(), sa.ForeignKey('loras.id', ondelete='RESTRICT'), primary_key=True),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('weight', sa.Numeric(8, 4), nullable=False))


def downgrade():
    op.drop_table('image_loras')
    op.drop_table('loras')
