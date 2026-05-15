"""add_seccomp_learn_change_type

Revision ID: c1d2e3f4a5b6
Revises: e4a56443300c
Create Date: 2026-05-14 03:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, None] = '255828ad9c81'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'seccomp_learn'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'configure_seccomp'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values
    pass
