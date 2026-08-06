"""add_mongodb_rs_upgrade_change_type

Revision ID: sdu003
Revises: sdu002
Create Date: 2026-08-06 00:02:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'sdu003'
down_revision: Union[str, None] = 'sdu002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'mongodb_rs_upgrade'")


def downgrade() -> None:
    pass  # PostgreSQL cannot drop enum values
