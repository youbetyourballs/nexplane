# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""add ad_dc_parallel_upgrade change type (idempotent guard)

Revision ID: 081
Revises: 079
Create Date: 2026-07-23

Note: the enum value was originally planned in r800s9t0u1v2 (a branch that
was never applied to production). This migration applies the value directly
from the 079 head and is safe to run multiple times due to IF NOT EXISTS.
"""

from alembic import op

revision = "081"
down_revision = "079"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TYPE changetype ADD VALUE IF NOT EXISTS 'ad_dc_parallel_upgrade'"
    )


def downgrade():
    # PostgreSQL does not support removing enum values without full type recreation.
    pass
