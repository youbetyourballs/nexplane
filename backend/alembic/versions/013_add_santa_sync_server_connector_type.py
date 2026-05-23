"""add santa_sync_server connector type and macos_fleet asset type

Revision ID: 013
Revises: f510f4f16763
Create Date: 2026-05-23
"""
from alembic import op

revision = '013'
down_revision = 'f510f4f16763'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE connector_type ADD VALUE IF NOT EXISTS 'santa_sync_server'")
    op.execute("ALTER TYPE asset_type ADD VALUE IF NOT EXISTS 'macos_fleet'")


def downgrade():
    pass
