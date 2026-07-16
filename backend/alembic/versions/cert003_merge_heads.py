# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Merge cert002 and merge002 heads."""

from typing import Union
from alembic import op

revision: str = "cert003_merge_heads"
down_revision: Union[tuple, None] = ("cert002_assettype_secrets", "merge002_ct001_presst001")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
