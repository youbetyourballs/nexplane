# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""infrastructure memory: asset_dependencies table + certificate/network metadata conventions

Revision ID: mem001
Revises: mem000_merge
Create Date: 2026-07-02
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "mem001"
down_revision = "mem000_merge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "asset_dependencies",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "dependent_asset_id",
            UUID(as_uuid=True),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            nullable=False,
            comment="The asset that depends on another (the consumer)",
        ),
        sa.Column(
            "dependency_asset_id",
            UUID(as_uuid=True),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            nullable=False,
            comment="The asset being depended on (the provider)",
        ),
        sa.Column(
            "dependency_type",
            sa.String(100),
            nullable=False,
            comment="e.g. uses_cert, connects_to, hosted_on, listens_on_port",
        ),
        sa.Column("dep_metadata", JSONB, nullable=False, server_default="{}"),
        sa.Column("source", sa.String(50), nullable=True,
                  comment="manual | cr_execution | discovery"),
        sa.Column(
            "discovered_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "organization_id",
            "dependent_asset_id",
            "dependency_asset_id",
            "dependency_type",
            name="uq_asset_dependencies_org_dep_type",
        ),
    )
    op.create_index(
        "ix_asset_dependencies_org_dependent",
        "asset_dependencies",
        ["organization_id", "dependent_asset_id"],
    )
    op.create_index(
        "ix_asset_dependencies_org_dependency",
        "asset_dependencies",
        ["organization_id", "dependency_asset_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_asset_dependencies_org_dependency", table_name="asset_dependencies")
    op.drop_index("ix_asset_dependencies_org_dependent", table_name="asset_dependencies")
    op.drop_table("asset_dependencies")
