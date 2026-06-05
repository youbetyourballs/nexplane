"""add datadog change types to change_type enum

Revision ID: k060e9f2g3h4
Revises: j950d8e1f2g3
Create Date: 2026-06-05
"""
from alembic import op

revision = "k060e9f2g3h4"
down_revision = "j950d8e1f2g3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'datadog_mute_host'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'datadog_unmute_host'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'datadog_create_monitor'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'datadog_send_event'")


def downgrade() -> None:
    pass
