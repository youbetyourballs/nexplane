# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""add tunnel_connection_tokens table

Revision ID: tunnel001
Revises: rt3_connector_network
Create Date: 2026-07-02
"""
from typing import Union
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from alembic import op

revision: str = "tunnel001"
down_revision: Union[str, None] = "rt3_connector_network"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tunnel_connection_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "agent_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agent_registrations.id"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_tunnel_connection_tokens_token_hash",
        "tunnel_connection_tokens",
        ["token_hash"],
    )
    op.create_index(
        "ix_tunnel_connection_tokens_agent_id",
        "tunnel_connection_tokens",
        ["agent_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_tunnel_connection_tokens_agent_id",
        table_name="tunnel_connection_tokens",
    )
    op.drop_index(
        "ix_tunnel_connection_tokens_token_hash",
        table_name="tunnel_connection_tokens",
    )
    op.drop_table("tunnel_connection_tokens")
