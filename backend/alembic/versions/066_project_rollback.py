"""add project rollback tables

Revision ID: 066_project_rollback
Revises: 065_ldap_change_type
Create Date: 2026-05-31
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "066_project_rollback"
down_revision = "065_ldap_change_type"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE project_status ADD VALUE IF NOT EXISTS 'rolling_back'")

    op.execute(
        "CREATE TYPE project_rollback_status AS ENUM "
        "('pending','running','paused','awaiting_user','completed','failed')"
    )
    op.execute(
        "CREATE TYPE project_rollback_trigger AS ENUM "
        "('manual','execution_failure','soak_health_check')"
    )
    op.execute(
        "CREATE TYPE rollback_step_status AS ENUM "
        "('pending','running','skipped','completed','failed','awaiting_user')"
    )
    op.execute(
        "CREATE TYPE rollback_kind AS ENUM "
        "('standard','reconstitution','permanent_no_backup')"
    )

    op.execute("""
        CREATE TABLE project_rollbacks (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id UUID NOT NULL REFERENCES projects(id),
            status project_rollback_status NOT NULL,
            trigger project_rollback_trigger NOT NULL,
            triggered_by_user_id UUID REFERENCES users(id),
            triggered_by_cr_id UUID REFERENCES change_requests(id),
            current_step INTEGER NOT NULL DEFAULT 0,
            notes TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            started_at TIMESTAMPTZ,
            paused_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ
        )
    """)

    op.execute("""
        CREATE TABLE project_rollback_steps (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_rollback_id UUID NOT NULL REFERENCES project_rollbacks(id),
            change_request_id UUID NOT NULL REFERENCES change_requests(id),
            sequence_order INTEGER NOT NULL,
            status rollback_step_status NOT NULL,
            rollback_kind rollback_kind NOT NULL,
            backup_cr_id UUID REFERENCES change_requests(id),
            started_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            result JSONB
        )
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS project_rollback_steps")
    op.execute("DROP TABLE IF EXISTS project_rollbacks")
    op.execute("DROP TYPE IF EXISTS rollback_kind")
    op.execute("DROP TYPE IF EXISTS rollback_step_status")
    op.execute("DROP TYPE IF EXISTS project_rollback_trigger")
    op.execute("DROP TYPE IF EXISTS project_rollback_status")
