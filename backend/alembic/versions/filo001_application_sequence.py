# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""FILO rollback stack: add application_sequence and applied_at to change_requests

Revision ID: filo001
Revises: cui1_catalog_action
Create Date: 2026-07-02
"""

from alembic import op
import sqlalchemy as sa

revision = "filo001"
down_revision = "cui1_catalog_action"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("change_requests", sa.Column("application_sequence", sa.Integer(), nullable=True))
    op.add_column("change_requests", sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True))
    # Index on application_sequence for fast ordering queries
    op.create_index(
        "ix_change_requests_application_sequence",
        "change_requests",
        ["application_sequence"],
    )


def downgrade():
    op.drop_index("ix_change_requests_application_sequence", table_name="change_requests")
    op.drop_column("change_requests", "applied_at")
    op.drop_column("change_requests", "application_sequence")
