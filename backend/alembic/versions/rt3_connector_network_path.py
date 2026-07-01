# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""connector network_path + tls skip verify

Revision ID: rt3_connector_network
Revises: rt2_agent_tunnel
"""
from alembic import op
import sqlalchemy as sa

revision = "rt3_connector_network"
down_revision = "rt2_agent_tunnel"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("connectors", sa.Column("network_path", sa.String(length=64), nullable=False, server_default="direct"))
    op.add_column("connectors", sa.Column("network_tls_skip_verify", sa.Boolean(), nullable=False, server_default=sa.text("false")))


def downgrade() -> None:
    op.drop_column("connectors", "network_tls_skip_verify")
    op.drop_column("connectors", "network_path")
