# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""add tunnel_max_concurrent to agent_registrations

Revision ID: tunnel002
Revises: tunnel001
Create Date: 2026-07-02
"""
from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "tunnel002"
down_revision: Union[str, None] = "tunnel001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_registrations",
        sa.Column(
            "tunnel_max_concurrent",
            sa.Integer(),
            nullable=False,
            server_default="10",
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_registrations", "tunnel_max_concurrent")
