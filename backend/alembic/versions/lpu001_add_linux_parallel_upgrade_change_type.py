"""add linux_parallel_upgrade change type

Revision ID: lpu001
Revises: 081, r800s9t0u1v2
Create Date: 2026-07-29
"""
from alembic import op

revision = 'lpu001'
down_revision = ('081', 'r800s9t0u1v2')
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'linux_parallel_upgrade'")


def downgrade():
    pass
