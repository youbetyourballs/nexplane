# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""add generic catalog_action change type

Revision ID: cui1_catalog_action
Revises: rt3_connector_network
"""
from alembic import op

revision = "cui1_catalog_action"
down_revision = "rt3_connector_network"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'catalog_action'")


def downgrade() -> None:
    pass
