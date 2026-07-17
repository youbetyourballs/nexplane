# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""add credential_rotation_fanout change_type

Revision ID: cred002_credential_rotation_fanout
Revises: cert003_merge_heads
Create Date: 2026-07-17
"""
from typing import Union
from alembic import op

revision: str = "cred002_credential_rotation_fanout"
down_revision: Union[str, None] = "cert003_merge_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'credential_rotation_fanout'")


def downgrade() -> None:
    pass
