# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""add db_major_version_upgrade change type

Revision ID: 078
Revises: 077
Create Date: 2026-07-23
"""

from alembic import op

revision = "078"
down_revision = "cert005"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'db_major_version_upgrade'"
    )


def downgrade():
    # PostgreSQL does not support removing enum values without a full type recreate.
    # To downgrade: recreate the enum without this value and cast all columns.
    # Omitting for safety — remove the value manually if rollback required.
    pass
