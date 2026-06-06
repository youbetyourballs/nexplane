"""add macos_santa_install to change_type enum

Revision ID: k160f0g2h3i4
Revises: j950d8e1f2g3
Create Date: 2026-06-05
"""
from alembic import op

revision = "k160f0g2h3i4"
down_revision = "070_add_runbook_cron_schedule"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'macos_santa_install'")


def downgrade() -> None:
    pass
