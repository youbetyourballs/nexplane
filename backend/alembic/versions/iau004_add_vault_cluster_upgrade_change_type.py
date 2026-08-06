"""add vault_cluster_upgrade change type

Revision ID: iau004
Revises: iau003
Create Date: 2026-08-05
"""
from alembic import op

revision = 'iau004'
down_revision = 'iau003'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'vault_cluster_upgrade'")


def downgrade():
    pass
