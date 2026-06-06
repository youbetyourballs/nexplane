"""Add recurring_job_policies table and policy_id FK on recurring_jobs

Revision ID: 073
Revises: 072
Create Date: 2026-06-05
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "073"
down_revision = "072"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "recurring_job_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("allowed_change_types", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("max_risk_level", sa.String(20), nullable=False, server_default="medium"),
        sa.Column("enabled", sa.Boolean, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.add_column(
        "recurring_jobs",
        sa.Column(
            "policy_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("recurring_job_policies.id"),
            nullable=True,
        ),
    )


def downgrade():
    op.drop_column("recurring_jobs", "policy_id")
    op.drop_table("recurring_job_policies")
