"""add darwin to os_type enum

Revision ID: 069_add_darwin_os_type
Revises: 068_ebpf_policy_types
Create Date: 2026-06-03
"""
from alembic import op

revision = '069_add_darwin_os_type'
down_revision = '068_ebpf_policy_types'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE os_type ADD VALUE IF NOT EXISTS 'darwin'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values without recreating the type
    pass
