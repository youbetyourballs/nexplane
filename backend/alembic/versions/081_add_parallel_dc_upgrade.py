# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""add ad_dc_parallel_upgrade change type (idempotent guard)

Revision ID: 081
Revises: 079, r800s9t0u1v2
Create Date: 2026-07-23

Note: the enum value was originally added in r800s9t0u1v2. This migration
serves as an explicit named checkpoint for the parallel DC upgrade feature
branch and is safe to run multiple times due to IF NOT EXISTS.
It also merges the two divergent heads (079 and r800s9t0u1v2).
"""

from alembic import op

revision = "081"
down_revision = ("079", "r800s9t0u1v2")
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TYPE changetype ADD VALUE IF NOT EXISTS 'ad_dc_parallel_upgrade'"
    )


def downgrade():
    # PostgreSQL does not support removing enum values without full type recreation.
    pass
