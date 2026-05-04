"""add tailscale connector type

Revision ID: 020
Revises: 019
Create Date: 2026-05-03
"""
from alembic import op

revision = '020'
down_revision = '019'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE connector_type ADD VALUE IF NOT EXISTS 'tailscale'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'tailscale_join'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'tailscale_remove'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'deploy_nexplane_agent'")


def downgrade():
    pass
