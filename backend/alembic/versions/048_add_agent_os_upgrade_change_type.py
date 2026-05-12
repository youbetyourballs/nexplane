"""add agent_os_upgrade change type

Revision ID: 048
Revises: 047
Create Date: 2026-05-12
"""
from alembic import op

revision = '048'
down_revision = '047'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'agent_os_upgrade'")


def downgrade():
    pass
