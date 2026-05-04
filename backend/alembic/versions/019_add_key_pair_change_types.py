"""add key_pair asset type and key_pair_create/ssm_command change types

Revision ID: 019
Revises: 018
Create Date: 2026-05-03
"""
from alembic import op

revision = '019'
down_revision = '018'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE asset_type ADD VALUE IF NOT EXISTS 'key_pair'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'key_pair_create'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'ssm_command'")


def downgrade():
    pass
