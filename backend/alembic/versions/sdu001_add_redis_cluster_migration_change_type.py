"""add_redis_cluster_migration_change_type

Revision ID: sdu001
Revises: 20260803_001
Create Date: 2026-08-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'sdu001'
down_revision: Union[str, None] = '20260803_001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'redis_cluster_migration'")


def downgrade() -> None:
    pass  # PostgreSQL cannot drop enum values
