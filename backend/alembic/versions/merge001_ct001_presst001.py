# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Merge ct001 and presst001 heads

Revision ID: merge002_ct001_presst001
Revises: ct001, presst001
Create Date: 2026-07-15
"""

from typing import Union
from alembic import op

revision: str = "merge002_ct001_presst001"
down_revision: Union[tuple, None] = ("ct001", "presst001")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
