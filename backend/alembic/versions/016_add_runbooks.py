"""add runbooks

Revision ID: 016
Revises: 015
Create Date: 2026-05-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "runbooks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("tags", postgresql.ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("is_seed", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_runbooks_organization_id", "runbooks", ["organization_id"])

    op.create_table(
        "runbook_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("runbook_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("runbooks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_step_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("runbook_steps.id"), nullable=True),
        sa.Column("step_number", sa.Integer, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("change_type", sa.String(128), nullable=True),
        sa.Column("parameters", postgresql.JSONB, nullable=True),
        sa.Column("asset_selector", postgresql.JSONB, nullable=True),
        sa.Column("condition_expr", sa.Text, nullable=True),
        sa.Column("on_true_step", sa.Integer, nullable=True),
        sa.Column("on_false_step", sa.Integer, nullable=True),
        sa.Column("prompt", sa.Text, nullable=True),
        sa.Column("required_role", sa.String(64), nullable=True),
        sa.Column("timeout_hours", sa.Integer, nullable=True),
        sa.Column("on_timeout", sa.String(32), nullable=True),
        sa.Column("on_failure", sa.String(32), nullable=False, server_default="abort"),
    )
    op.create_index("ix_runbook_steps_runbook_id", "runbook_steps", ["runbook_id"])

    op.create_table(
        "runbook_executions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("runbook_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("runbooks.id"), nullable=False),
        sa.Column("runbook_version", sa.Integer, nullable=False),
        sa.Column("runbook_snapshot", postgresql.JSONB, nullable=False),
        sa.Column("triggered_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("triggered_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("context", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column("current_step", sa.Integer, nullable=False, server_default="1"),
    )
    op.create_index("ix_runbook_executions_runbook_id", "runbook_executions", ["runbook_id"])
    op.create_index("ix_runbook_executions_status", "runbook_executions", ["status"])

    op.create_table(
        "runbook_step_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("execution_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("runbook_executions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_number", sa.Integer, nullable=False),
        sa.Column("step_name", sa.String(255), nullable=False),
        sa.Column("step_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("change_request_ids", postgresql.ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("result", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("error_message", sa.Text, nullable=True),
    )
    op.create_index("ix_runbook_step_results_execution_id", "runbook_step_results", ["execution_id"])

    # Source tracking on existing change_requests table
    op.add_column("change_requests", sa.Column("source", sa.String(32), nullable=True))
    op.add_column("change_requests", sa.Column(
        "runbook_execution_id", postgresql.UUID(as_uuid=True), nullable=True
    ))


def downgrade():
    op.drop_column("change_requests", "runbook_execution_id")
    op.drop_column("change_requests", "source")
    op.drop_index("ix_runbook_step_results_execution_id", table_name="runbook_step_results")
    op.drop_table("runbook_step_results")
    op.drop_index("ix_runbook_executions_status", table_name="runbook_executions")
    op.drop_index("ix_runbook_executions_runbook_id", table_name="runbook_executions")
    op.drop_table("runbook_executions")
    op.drop_index("ix_runbook_steps_runbook_id", table_name="runbook_steps")
    op.drop_table("runbook_steps")
    op.drop_index("ix_runbooks_organization_id", table_name="runbooks")
    op.drop_table("runbooks")
