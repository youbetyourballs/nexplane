# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""add notification_routing_rules table

Revision ID: notif001
Revises: mem001
Create Date: 2026-07-02
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "notif001"
down_revision = "mem001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "notification_routing_rules" not in inspector.get_table_names():
        op.create_table(
            "notification_routing_rules",
            sa.Column("id", sa.String, primary_key=True),
            sa.Column("name", sa.String, nullable=False),
            sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
            sa.Column("priority", sa.Integer, nullable=False, server_default=sa.text("100")),
            sa.Column("match_cr_types", JSONB, nullable=True),
            sa.Column("match_severities", JSONB, nullable=True),
            sa.Column("match_asset_tags", JSONB, nullable=True),
            sa.Column("match_connector_types", JSONB, nullable=True),
            sa.Column("match_status", JSONB, nullable=True),
            sa.Column("notify_user_ids", JSONB, nullable=True),
            sa.Column("notify_role_ids", JSONB, nullable=True),
            sa.Column("notify_channels", JSONB, nullable=True),
            sa.Column("suppress_default", sa.Boolean, nullable=False, server_default=sa.text("false")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_by", sa.String, sa.ForeignKey("users.id"), nullable=True),
        )
        op.create_index(
            "ix_notification_routing_rules_priority",
            "notification_routing_rules",
            ["priority"],
        )


def downgrade() -> None:
    op.drop_index("ix_notification_routing_rules_priority", table_name="notification_routing_rules")
    op.drop_table("notification_routing_rules")
