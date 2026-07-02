# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""add mcp_intelligence_cache table

Revision ID: intel001
Revises: notif001
Create Date: 2026-07-02
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "intel001"
down_revision = "notif001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "mcp_intelligence_cache" not in inspector.get_table_names():
        op.create_table(
            "mcp_intelligence_cache",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "org_id",
                UUID(as_uuid=True),
                sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "asset_id",
                UUID(as_uuid=True),
                sa.ForeignKey("assets.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("tool_name", sa.String(128), nullable=False),
            sa.Column("result", JSONB, nullable=False),
            sa.Column(
                "cached_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column(
                "ttl_seconds",
                sa.Integer,
                nullable=False,
                server_default=sa.text("300"),
            ),
        )
        op.create_index(
            "ix_mcp_intel_cache_lookup",
            "mcp_intelligence_cache",
            ["org_id", "asset_id", "tool_name"],
        )


def downgrade() -> None:
    op.drop_index("ix_mcp_intel_cache_lookup", table_name="mcp_intelligence_cache")
    op.drop_table("mcp_intelligence_cache")
