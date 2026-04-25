"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-01-01 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSON

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("email", sa.String(320), unique=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("role", sa.Enum("admin", "security_operator", "approver", "auditor", name="user_role"), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "assets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("asset_type", sa.Enum("server", "cloud_account", "dns_zone", "firewall", "identity_provider", "application", name="asset_type"), nullable=False),
        sa.Column("environment", sa.Enum("dev", "staging", "prod", name="environment"), nullable=False),
        sa.Column("criticality", sa.Enum("low", "medium", "high", "critical", name="criticality"), nullable=False),
        sa.Column("metadata", JSON, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "connectors",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("connector_type", sa.Enum("aws_mock", "azure_mock", "cloudflare_mock", "okta_mock", "paloalto_mock", "ssh_runner_mock", name="connector_type"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.Enum("active", "inactive", "error", name="connector_status"), server_default="active"),
        sa.Column("scoped_permissions", JSON, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "change_requests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("requester_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("change_type", sa.Enum("dns_update", "snapshot_asset", "security_group_update", "key_rotation", "telemetry_agent_deploy", "remote_command", "microsegmentation_policy", name="change_type"), nullable=False),
        sa.Column("target_asset_ids", JSON, nullable=False, server_default="[]"),
        sa.Column("desired_outcome", JSON, nullable=False, server_default="{}"),
        sa.Column("risk_level", sa.Enum("low", "medium", "high", "critical", name="risk_level"), server_default="medium"),
        sa.Column("status", sa.Enum("draft", "planned", "safety_review", "awaiting_approval", "approved", "executing", "verifying", "completed", "failed", "rolled_back", "rejected", name="change_request_status"), server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "change_plans",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("change_request_id", UUID(as_uuid=True), sa.ForeignKey("change_requests.id"), nullable=False, unique=True),
        sa.Column("generated_steps", JSON, nullable=False, server_default="[]"),
        sa.Column("preflight_checks", JSON, nullable=False, server_default="[]"),
        sa.Column("blast_radius", JSON, nullable=False, server_default="{}"),
        sa.Column("rollback_plan", JSON, nullable=False, server_default="{}"),
        sa.Column("verification_plan", JSON, nullable=False, server_default="{}"),
        sa.Column("generated_by", sa.Enum("system", "ai_mock", "human", name="plan_generated_by"), server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "approvals",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("change_request_id", UUID(as_uuid=True), sa.ForeignKey("change_requests.id"), nullable=False),
        sa.Column("approver_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("decision", sa.Enum("approved", "rejected", name="approval_decision"), nullable=False),
        sa.Column("comment", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "execution_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("change_request_id", UUID(as_uuid=True), sa.ForeignKey("change_requests.id"), nullable=False),
        sa.Column("workflow_id", sa.String(255), nullable=False, unique=True),
        sa.Column("status", sa.Enum("pending", "running", "completed", "failed", "rolling_back", "rolled_back", name="execution_status"), server_default="pending"),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", JSON, nullable=False, server_default="{}"),
    )

    op.create_table(
        "audit_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("actor_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("change_request_id", UUID(as_uuid=True), sa.ForeignKey("change_requests.id"), nullable=True),
        sa.Column("event_type", sa.String(255), nullable=False),
        sa.Column("event_payload", JSON, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_audit_events_event_type", "audit_events", ["event_type"])
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("execution_runs")
    op.drop_table("approvals")
    op.drop_table("change_plans")
    op.drop_table("change_requests")
    op.drop_table("connectors")
    op.drop_table("assets")
    op.drop_table("users")
    op.drop_table("organizations")

    for enum_name in ["user_role", "asset_type", "environment", "criticality",
                      "connector_type", "connector_status", "change_type", "risk_level",
                      "change_request_status", "plan_generated_by", "approval_decision", "execution_status"]:
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
