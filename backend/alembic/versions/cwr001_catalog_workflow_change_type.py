# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""add catalog_workflow change type

Revision ID: cwr001_catalog_workflow
Revises: backup001
Create Date: 2026-07-04
"""

revision = "cwr001_catalog_workflow"
down_revision = "backup001"
branch_labels = None
depends_on = None

from alembic import op


def upgrade() -> None:
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'catalog_workflow'")


def downgrade() -> None:
    # Postgres does not support removing enum values; downgrade is intentionally a no-op.
    pass
