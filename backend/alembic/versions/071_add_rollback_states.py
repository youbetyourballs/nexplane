"""Add rollback_partial, rollback_failed, manual_recovery_required to change_request_status enum

Revision ID: 071
Revises: 070_add_runbook_cron_schedule
Create Date: 2026-06-05
"""
from alembic import op

revision = "071"
down_revision = "k160f0g2h3i4"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE change_request_status ADD VALUE IF NOT EXISTS 'rollback_partial'")
    op.execute("ALTER TYPE change_request_status ADD VALUE IF NOT EXISTS 'rollback_failed'")
    op.execute("ALTER TYPE change_request_status ADD VALUE IF NOT EXISTS 'manual_recovery_required'")


def downgrade():
    # PostgreSQL does not support removing enum values.
    pass
