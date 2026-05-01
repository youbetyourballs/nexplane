"""add agent models and nexplane_agent connector type

Revision ID: 006
Revises: 005
Create Date: 2026-05-01 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, ENUM as PgEnum

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE connector_type ADD VALUE IF NOT EXISTS 'nexplane_agent'")

    op.execute("""
        DO $$ BEGIN
            CREATE TYPE os_type AS ENUM ('linux', 'windows');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE agent_job_status AS ENUM ('pending', 'running', 'completed', 'failed');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)

    op.add_column(
        "organization_settings",
        sa.Column("agent_secret_encrypted", sa.Text, nullable=True),
    )

    op.create_table(
        "agent_registrations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("machine_id", sa.String(255), nullable=False),
        sa.Column("asset_id", UUID(as_uuid=True), sa.ForeignKey("assets.id"), nullable=True),
        sa.Column("hostname", sa.String(255), nullable=False),
        sa.Column("os_type", PgEnum("linux", "windows", name="os_type", create_type=False), nullable=False),
        sa.Column("ip_addresses", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("os_version", sa.String(255), nullable=False, server_default=""),
        sa.Column("agent_version", sa.String(50), nullable=False, server_default=""),
        sa.Column("last_seen", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_unique_constraint(
        "uq_agent_registrations_org_machine",
        "agent_registrations",
        ["organization_id", "machine_id"],
    )

    op.create_table(
        "agent_jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("agent_registration_id", UUID(as_uuid=True), sa.ForeignKey("agent_registrations.id"), nullable=False),
        sa.Column("change_request_id", UUID(as_uuid=True), sa.ForeignKey("change_requests.id"), nullable=True),
        sa.Column("command", sa.String(100), nullable=False),
        sa.Column("parameters", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("hmac_signature", sa.String(64), nullable=False, server_default=""),
        sa.Column(
            "status",
            PgEnum("pending", "running", "completed", "failed", name="agent_job_status", create_type=False),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("result", sa.JSON, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("agent_jobs")
    op.drop_table("agent_registrations")
    op.drop_column("organization_settings", "agent_secret_encrypted")
    op.execute("DROP TYPE agent_job_status")
    op.execute("DROP TYPE os_type")
    # Note: 'nexplane_agent' is intentionally NOT removed from the connector_type enum.
    # PostgreSQL does not support ALTER TYPE ... DROP VALUE — removing an enum value
    # requires recreating the type, which risks data loss if any rows reference it.
