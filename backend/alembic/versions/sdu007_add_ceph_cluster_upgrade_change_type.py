"""add_ceph_cluster_upgrade_change_type

Revision ID: sdu007
Revises: sdu006
Create Date: 2026-08-06 00:06:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'sdu007'
down_revision: Union[str, None] = 'sdu006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'ceph_cluster_upgrade'")


def downgrade() -> None:
    pass  # PostgreSQL cannot drop enum values
