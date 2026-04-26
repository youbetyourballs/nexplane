"""add projects tables

Revision ID: 003
Revises: 002
Create Date: 2026-04-26 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("goal", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "status",
            sa.Enum("draft", "in_progress", "completed", "cancelled", name="project_status"),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_projects_organization_id", "projects", ["organization_id"])

    op.create_table(
        "project_change_requests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column(
            "change_request_id", UUID(as_uuid=True), sa.ForeignKey("change_requests.id"), nullable=False
        ),
        sa.Column("sequence_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("depends_on", sa.JSON, nullable=False, server_default="[]"),
    )
    op.create_index(
        "ix_project_change_requests_project_id", "project_change_requests", ["project_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_project_change_requests_project_id")
    op.drop_table("project_change_requests")
    op.drop_index("ix_projects_organization_id")
    op.drop_table("projects")
    op.execute("DROP TYPE IF EXISTS project_status")
