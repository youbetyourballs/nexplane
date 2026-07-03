# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""bkp001: backup_storage and recovery_tokens tables; update backup_targets

Revision ID: bkp001
Revises: filo001, proj002, tunnel002
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "bkp001"
down_revision = ("filo001", "proj002", "tunnel002")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "backup_storage",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("storage_type", sa.String(50), nullable=False),
        sa.Column("config", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("is_org_default", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "recovery_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("restore_cr_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("change_requests.id", ondelete="SET NULL"), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.add_column("backup_targets",
        sa.Column("storage_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("backup_storage.id", ondelete="SET NULL"), nullable=True))
    op.add_column("backup_targets",
        sa.Column("asset_type", sa.String(50), nullable=False, server_default="server"))


def downgrade() -> None:
    op.drop_column("backup_targets", "asset_type")
    op.drop_column("backup_targets", "storage_id")
    op.drop_table("recovery_tokens")
    op.drop_table("backup_storage")
