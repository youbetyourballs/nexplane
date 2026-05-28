"""add recurring_jobs table

Revision ID: 063_recurring_jobs
Revises: g620a5b7c8d9
Create Date: 2026-05-28
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "063_recurring_jobs"
down_revision: str | None = "g620a5b7c8d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE recurring_job_type AS ENUM ('backup', 'scheduled_restore', 'scheduled_op');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.create_table(
        "recurring_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("job_type", postgresql.ENUM("backup", "scheduled_restore", "scheduled_op",
                                        name="recurring_job_type", create_type=False), nullable=False),
        sa.Column("connector_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("connectors.id", ondelete="SET NULL"), nullable=True),
        sa.Column("action_id", sa.String(255), nullable=False),
        sa.Column("parameters", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("target_description", sa.Text, nullable=False),
        sa.Column("cron_expression", sa.String(100), nullable=False),
        sa.Column("schedule_preset", sa.String(50), nullable=True),
        sa.Column("schedule_hour", sa.Integer, nullable=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_cr_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("change_requests.id", ondelete="SET NULL"), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_recurring_jobs_organization_id", "recurring_jobs", ["organization_id"])


def downgrade() -> None:
    op.drop_index("ix_recurring_jobs_organization_id", table_name="recurring_jobs")
    op.drop_table("recurring_jobs")
    op.execute("DROP TYPE recurring_job_type")
