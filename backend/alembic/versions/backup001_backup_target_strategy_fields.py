# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Add backup_tier and capture_strategy to backup_targets

Revision ID: backup001
Revises: bkp002
Create Date: 2026-07-03
"""
from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "backup001"
down_revision: Union[str, None] = "bkp002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "backup_targets",
        sa.Column("backup_tier", sa.String(20), nullable=False, server_default="machine"),
    )
    op.add_column(
        "backup_targets",
        sa.Column("capture_strategy", sa.String(50), nullable=False, server_default="ebs_snapshot"),
    )


def downgrade() -> None:
    op.drop_column("backup_targets", "capture_strategy")
    op.drop_column("backup_targets", "backup_tier")
