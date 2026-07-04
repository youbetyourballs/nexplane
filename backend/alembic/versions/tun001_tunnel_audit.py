# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""add tunnel_dial_audit table

Revision ID: tun001_tunnel_audit
Revises: cwr001_catalog_workflow
Create Date: 2026-07-04
"""
from typing import Union
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "tun001_tunnel_audit"
down_revision: Union[str, None] = "cwr001_catalog_workflow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tunnel_dial_audit",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agent_registrations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("connector_type", sa.String(64), nullable=True),
        sa.Column("destination_host", sa.String(255), nullable=False),
        sa.Column("destination_port", sa.Integer(), nullable=False),
        sa.Column("bytes_sent", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("bytes_recv", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("close_reason", sa.String(64), nullable=True),
    )
    op.create_index("ix_tunnel_dial_audit_agent_id", "tunnel_dial_audit", ["agent_id"])
    op.create_index("ix_tunnel_dial_audit_opened_at", "tunnel_dial_audit", ["opened_at"])


def downgrade() -> None:
    op.drop_index("ix_tunnel_dial_audit_opened_at", "tunnel_dial_audit")
    op.drop_index("ix_tunnel_dial_audit_agent_id", "tunnel_dial_audit")
    op.drop_table("tunnel_dial_audit")
