# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""asset_connectors join table

Revision ID: cert005
Revises: cert004
Create Date: 2026-07-21
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision = "cert005"
down_revision = "cert004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "asset_connectors",
        sa.Column("asset_id", PG_UUID(as_uuid=True), sa.ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True, nullable=False),
        sa.Column("connector_id", PG_UUID(as_uuid=True), sa.ForeignKey("connectors.id", ondelete="CASCADE"), primary_key=True, nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_asset_connectors_asset_id", "asset_connectors", ["asset_id"])
    op.create_index("ix_asset_connectors_connector_id", "asset_connectors", ["connector_id"])

    # Backfill from existing assets.connector_id
    op.execute("""
        INSERT INTO asset_connectors (asset_id, connector_id)
        SELECT id, connector_id
        FROM assets
        WHERE connector_id IS NOT NULL
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.drop_index("ix_asset_connectors_connector_id", table_name="asset_connectors")
    op.drop_index("ix_asset_connectors_asset_id", table_name="asset_connectors")
    op.drop_table("asset_connectors")
