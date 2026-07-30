"""add windows_parallel_migration change type

Revision ID: wpm001
Revises: lpu001
Create Date: 2026-07-30
"""
from alembic import op

revision = 'wpm001'
down_revision = 'lpu001'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'windows_parallel_migration'")


def downgrade():
    pass
