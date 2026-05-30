"""add security_policy_soak_sessions and security_policy_baselines tables

Revision ID: 066_security_policy_soak
Revises: 065_ldap_change_type
Create Date: 2026-05-30
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "066_security_policy_soak"
down_revision: str | None = "065_ldap_change_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "security_policy_soak_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("policy_type", sa.String(32), nullable=False, server_default="seccomp"),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("window_seconds", sa.Integer, nullable=False, server_default="600"),
        sa.Column("asset_ids", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("raw_observations", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("synthesized_profile", postgresql.JSONB, nullable=True),
        sa.Column("baseline_delta", postgresql.JSONB, nullable=True),
        sa.Column("partial", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("cr_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("change_requests.id", ondelete="SET NULL"), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('running','stopped','synthesized','cr_proposed')",
            name="ck_soak_sessions_status",
        ),
        sa.CheckConstraint(
            "policy_type IN ('seccomp','apparmor','selinux','network_policy')",
            name="ck_soak_sessions_policy_type",
        ),
    )
    op.create_index("ix_soak_sessions_organization_id", "security_policy_soak_sessions", ["organization_id"])
    op.create_index("ix_soak_sessions_project_id", "security_policy_soak_sessions", ["project_id"])

    op.create_table(
        "security_policy_baselines",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("policy_type", sa.String(32), nullable=False, server_default="seccomp"),
        sa.Column("profile", postgresql.JSONB, nullable=False),
        sa.Column("cr_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("change_requests.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "policy_type", name="uq_baselines_project_policy_type"),
    )
    op.create_index("ix_baselines_organization_id", "security_policy_baselines", ["organization_id"])
    op.create_index("ix_baselines_project_id", "security_policy_baselines", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_baselines_project_id", table_name="security_policy_baselines")
    op.drop_index("ix_baselines_organization_id", table_name="security_policy_baselines")
    op.drop_table("security_policy_baselines")
    op.drop_index("ix_soak_sessions_project_id", table_name="security_policy_soak_sessions")
    op.drop_index("ix_soak_sessions_organization_id", table_name="security_policy_soak_sessions")
    op.drop_table("security_policy_soak_sessions")
