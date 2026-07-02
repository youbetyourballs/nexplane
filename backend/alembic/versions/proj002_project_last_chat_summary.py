# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""add last_chat_summary to projects

Revision ID: proj002
Revises: proj001
Create Date: 2026-07-02
"""
from alembic import op
import sqlalchemy as sa

revision = "proj002"
down_revision = "proj001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    cols = [c["name"] for c in inspector.get_columns("projects")]
    if "last_chat_summary" not in cols:
        op.add_column("projects", sa.Column("last_chat_summary", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "last_chat_summary")
