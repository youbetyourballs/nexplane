"""Add cron_schedule and last_scheduled_run_at to runbooks

Revision ID: 070_add_runbook_cron_schedule
Revises: l170f0g3h4i5
Create Date: 2026-06-05

"""
from alembic import op
import sqlalchemy as sa

revision = "070_add_runbook_cron_schedule"
down_revision = "l170f0g3h4i5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runbooks", sa.Column("cron_schedule", sa.String(100), nullable=True))
    op.add_column("runbooks", sa.Column("last_scheduled_run_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("runbooks", "last_scheduled_run_at")
    op.drop_column("runbooks", "cron_schedule")
