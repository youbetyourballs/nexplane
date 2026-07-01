# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""agent reverse-tunnel fields

Adds opt-in reverse-tunnel connectivity fields to agent_registrations:
- tunnel_enabled (bool, default false)
- tunnel_allowlist (json list of "host:port" allowlist entries, nullable)

Additive + safe: existing rows get tunnel_enabled=false and a NULL allowlist
(treated as empty / deny-all by the authorizer).

See nexplane-deploy: docs/superpowers/specs/2026-07-01-agent-reverse-tunnel-design.md

Revision ID: rt2_agent_tunnel
Revises: q700m5n8o9p0
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "rt2_agent_tunnel"
down_revision: Union[str, None] = "q700m5n8o9p0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_registrations",
        sa.Column("tunnel_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "agent_registrations",
        sa.Column("tunnel_allowlist", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_registrations", "tunnel_allowlist")
    op.drop_column("agent_registrations", "tunnel_enabled")
