"""add cancelled to change_request_status enum

Revision ID: drift002
Revises: drift001
Create Date: 2026-08-20
"""
from alembic import op

revision = 'drift002'
down_revision = 'drift001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE change_request_status ADD VALUE IF NOT EXISTS 'cancelled'")


def downgrade() -> None:
    pass
