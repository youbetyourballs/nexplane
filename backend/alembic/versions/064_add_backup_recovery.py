"""add backup_targets table and artifact_refs on change_requests

Revision ID: 064_backup_recovery
Revises: 063_recurring_jobs
Create Date: 2026-05-28
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "064_backup_recovery"
down_revision: str | None = "063_recurring_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add artifact_refs JSONB column to change_requests
    op.add_column(
        "change_requests",
        sa.Column("artifact_refs", postgresql.JSONB, nullable=True),
    )

    # Create backup_targets table
    # status uses String(32) with a CHECK constraint rather than a PG enum type
    # to avoid double-CREATE issues with asyncpg and stay consistent with
    # other status columns in this codebase (see drift_alerts, etc.)
    op.create_table(
        "backup_targets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("recurring_job_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("recurring_jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("assets.id", ondelete="SET NULL"), nullable=True),
        sa.Column("target_description", sa.Text, nullable=False),
        sa.Column("expected_cadence_hours", sa.Integer, nullable=False, server_default="24"),
        sa.Column("last_successful_backup_cr_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("change_requests.id", ondelete="SET NULL"), nullable=True),
        sa.Column("last_successful_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="unprotected",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('healthy', 'overdue', 'unprotected')",
            name="ck_backup_targets_status",
        ),
    )
    op.create_index("ix_backup_targets_organization_id", "backup_targets", ["organization_id"])
    op.create_index("ix_backup_targets_recurring_job_id", "backup_targets", ["recurring_job_id"])


def downgrade() -> None:
    op.drop_index("ix_backup_targets_recurring_job_id", table_name="backup_targets")
    op.drop_index("ix_backup_targets_organization_id", table_name="backup_targets")
    op.drop_table("backup_targets")
    op.drop_column("change_requests", "artifact_refs")
