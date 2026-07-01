# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""add platform_upgrade cr type

Revision ID: p600j4k7l8m9
Revises: o500i3j6k7l8
Create Date: 2026-06-30

"""
from alembic import op

revision = 'p600j4k7l8m9'
down_revision = 'o500i3j6k7l8'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'platform_upgrade'")


def downgrade():
    pass  # Postgres does not support removing enum values
