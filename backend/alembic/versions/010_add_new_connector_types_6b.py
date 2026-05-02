"""add new connector types for 6b: gcp, runzero, wiz, entra_id

Revision ID: 010
Revises: 009
Create Date: 2026-05-01
"""
from alembic import op

revision = '010'
down_revision = '009'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE connector_type ADD VALUE IF NOT EXISTS 'gcp'")
    op.execute("ALTER TYPE connector_type ADD VALUE IF NOT EXISTS 'runzero'")
    op.execute("ALTER TYPE connector_type ADD VALUE IF NOT EXISTS 'wiz'")
    op.execute("ALTER TYPE connector_type ADD VALUE IF NOT EXISTS 'entra_id'")


def downgrade():
    # PostgreSQL does not support removing enum values without recreation
    pass
