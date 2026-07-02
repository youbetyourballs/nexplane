# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""add project_success_criteria table

Revision ID: proj001
Revises: pc001
Create Date: 2026-07-02
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID, ENUM as PG_ENUM

revision = "proj001"
down_revision = "pc001"
branch_labels = None
depends_on = None

criteriatype = PG_ENUM(
    "cr_completed", "host_state_check", "service_check", "port_check", "manual",
    name="criteriatype",
)
criteriaresult = PG_ENUM(
    "pass", "fail", "pending_manual", "not_checked",
    name="criteriaresult",
)


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Create enums idempotently via PG_ENUM.create(checkfirst=True)
    criteriatype.create(conn, checkfirst=True)
    criteriaresult.create(conn, checkfirst=True)

    if "project_success_criteria" not in inspector.get_table_names():
        op.create_table(
            "project_success_criteria",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "project_id",
                UUID(as_uuid=True),
                sa.ForeignKey("projects.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "type",
                PG_ENUM(
                    "cr_completed", "host_state_check", "service_check",
                    "port_check", "manual",
                    name="criteriatype",
                    create_type=False,
                ),
                nullable=False,
            ),
            sa.Column("description", sa.String(500), nullable=False),
            sa.Column("assertion", JSONB, nullable=False, server_default=sa.text("'{}'")),
            sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "last_result",
                PG_ENUM(
                    "pass", "fail", "pending_manual", "not_checked",
                    name="criteriaresult",
                    create_type=False,
                ),
                nullable=False,
                server_default=sa.text("'not_checked'"),
            ),
            sa.Column("last_result_detail", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        op.create_index(
            "ix_project_success_criteria_project_id",
            "project_success_criteria",
            ["project_id"],
        )


def downgrade() -> None:
    op.drop_index(
        "ix_project_success_criteria_project_id",
        table_name="project_success_criteria",
    )
    op.drop_table("project_success_criteria")
    criteriatype.drop(op.get_bind(), checkfirst=True)
    criteriaresult.drop(op.get_bind(), checkfirst=True)
