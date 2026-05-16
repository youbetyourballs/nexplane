"""merge_numeric_and_hash_branches

Merges the numeric migration chain (049 -> 050 -> 051 -> 052 -> 053) with
the parallel hash-based migration chain (049 -> 165954e097c8 -> ... -> 184f1eea3cad).

Revision ID: 054_merge_branches
Revises: 053_ensure_notifications, 184f1eea3cad
Create Date: 2026-05-15 00:00:00.000000

"""
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = '054_merge_branches'
down_revision: Union[str, Sequence[str], None] = ('053_ensure_notifications', '184f1eea3cad')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass  # merge-only migration — no schema changes


def downgrade() -> None:
    pass
