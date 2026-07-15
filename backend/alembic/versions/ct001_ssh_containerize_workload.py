# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Add ssh_containerize_workload to change_type enum

Revision ID: ct001
Revises: tunnel002
Create Date: 2026-07-15
"""

from typing import Union
from alembic import op

revision: str = "ct001"
down_revision: Union[str, None] = "tunnel002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'ssh_containerize_workload'")


def downgrade() -> None:
    # Postgres enums cannot have values removed without a full type rebuild.
    pass
