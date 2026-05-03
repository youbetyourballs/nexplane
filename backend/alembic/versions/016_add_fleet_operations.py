"""add fleet operations: step_metadata, maintenance_window, fleet statuses

Revision ID: 016
Revises: 015
Create Date: 2026-05-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '016'
down_revision = '015'
branch_labels = None
depends_on = None


def upgrade():
    # Add step_metadata JSONB column to change_request
    op.add_column(
        "change_requests",
        sa.Column("step_metadata", postgresql.JSONB, nullable=True),
    )

    # Add new ChangeType enum values
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'rolling_restart'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'canary_config_push'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'distribute_file'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'fleet_health_check'")

    # Add new ChangeRequestStatus enum values
    op.execute("ALTER TYPE change_request_status ADD VALUE IF NOT EXISTS 'queued_for_maintenance'")
    op.execute("ALTER TYPE change_request_status ADD VALUE IF NOT EXISTS 'preflight_running'")
    op.execute("ALTER TYPE change_request_status ADD VALUE IF NOT EXISTS 'preflight_failed'")
    op.execute("ALTER TYPE change_request_status ADD VALUE IF NOT EXISTS 'batch_running'")
    op.execute("ALTER TYPE change_request_status ADD VALUE IF NOT EXISTS 'batch_aborted'")
    op.execute("ALTER TYPE change_request_status ADD VALUE IF NOT EXISTS 'completed_with_errors'")

    # Create maintenance_window table
    op.create_table(
        "maintenance_window",
        sa.Column("id",               sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("organization_id",  postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name",             sa.String(255), nullable=False),
        sa.Column("cron_schedule",    sa.String(100), nullable=False),
        sa.Column("duration_minutes", sa.Integer, nullable=False, server_default="60"),
        sa.Column("applies_to_tags",  postgresql.JSONB, nullable=True),
        sa.Column("enabled",          sa.Boolean, nullable=False, server_default="true"),
    )
    op.create_index("ix_maintenance_window_org", "maintenance_window", ["organization_id"])


def downgrade():
    op.drop_index("ix_maintenance_window_org", table_name="maintenance_window")
    op.drop_table("maintenance_window")
    op.drop_column("change_requests", "step_metadata")
    # Note: PostgreSQL does not support removing enum values; downgrade leaves fleet enum values in place
