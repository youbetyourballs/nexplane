# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""add credential_rotation change_type and paused status

Revision ID: cred001_credential_rotation
Revises: tun001_tunnel_audit
Create Date: 2026-07-15
"""
from typing import Union
from alembic import op

revision: str = "cred001_credential_rotation"
down_revision: Union[str, None] = "tun001_tunnel_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'credential_rotation'")
    op.execute("ALTER TYPE change_request_status ADD VALUE IF NOT EXISTS 'paused'")
    op.execute("ALTER TYPE change_request_status ADD VALUE IF NOT EXISTS 'rolled_back_with_warnings'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; leave in place
    pass
