"""add new asset types

Revision ID: 018
Revises: b3f7d2e9a1c5
Create Date: 2026-05-03
"""
from alembic import op

revision = '018'
down_revision = 'b3f7d2e9a1c5'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE asset_type ADD VALUE IF NOT EXISTS 'database'")
    op.execute("ALTER TYPE asset_type ADD VALUE IF NOT EXISTS 'storage_bucket'")
    op.execute("ALTER TYPE asset_type ADD VALUE IF NOT EXISTS 'load_balancer'")
    op.execute("ALTER TYPE asset_type ADD VALUE IF NOT EXISTS 'endpoint'")
    op.execute("ALTER TYPE asset_type ADD VALUE IF NOT EXISTS 'container_cluster'")


def downgrade():
    # PostgreSQL does not support removing enum values without recreating the type.
    pass
