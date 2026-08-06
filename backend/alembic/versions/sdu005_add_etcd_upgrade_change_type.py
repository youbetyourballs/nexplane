"""add_etcd_upgrade_change_type

Revision ID: sdu005
Revises: sdu004
Create Date: 2026-08-06 00:04:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'sdu005'
down_revision: Union[str, None] = 'sdu004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'etcd_upgrade'")


def downgrade() -> None:
    pass  # PostgreSQL cannot drop enum values
