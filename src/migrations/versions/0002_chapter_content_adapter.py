"""Chapter compatibility layouts and one-time import provenance.

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("chapters", "paragraphs", "timeline_items"):
        op.add_column(table, sa.Column("content_layout", JSONB(), nullable=True))
    op.add_column("chapters", sa.Column("legacy_import", JSONB(), nullable=True))


def downgrade():
    op.drop_column("chapters", "legacy_import")
    for table in ("timeline_items", "paragraphs", "chapters"):
        op.drop_column(table, "content_layout")
