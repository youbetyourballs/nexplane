# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Merge ir001 and tun001_tunnel_audit heads.

Revision ID: merge001
Revises: ir001, tun001_tunnel_audit
"""
from typing import Union
from alembic import op

revision: str = "merge001"
down_revision: Union[str, tuple] = ("ir001", "tun001_tunnel_audit")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
