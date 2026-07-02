# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""enable pg_trgm extension

Revision ID: pc001
Revises: intel001
Create Date: 2026-07-02
"""
from alembic import op

revision = 'pc001'
down_revision = 'intel001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")


def downgrade() -> None:
    pass  # intentionally no-op; removing trgm could break other uses
