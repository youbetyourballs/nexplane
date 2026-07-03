# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Add server_backup, server_snapshot, server_capture, restore_server to change_type enum

Revision ID: bkp002
Revises: bkp001
Create Date: 2026-07-03
"""

from alembic import op

revision = "bkp002"
down_revision = "bkp001"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'server_backup'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'server_snapshot'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'server_capture'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'restore_server'")


def downgrade():
    # PostgreSQL does not support removing enum values; downgrade is a no-op
    pass
