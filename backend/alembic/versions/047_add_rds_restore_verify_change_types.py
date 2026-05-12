"""add restore_rds_snapshot and verify_rds_backup change types

Revision ID: 047
Revises: 046
Create Date: 2026-05-11
"""
from alembic import op

revision = '047'
down_revision = '046'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'restore_rds_snapshot'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'verify_rds_backup'")


def downgrade():
    pass
