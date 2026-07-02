# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""merge tunnel002 and filo001 heads before infrastructure memory

Revision ID: mem000_merge
Revises: tunnel002, filo001
Create Date: 2026-07-02
"""
from alembic import op

revision = "mem000_merge"
down_revision = ("tunnel002", "filo001")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
