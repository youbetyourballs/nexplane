"""add_container_image_transfer_change_type

Revision ID: 20260803_001
Revises: 7736931efb06
Create Date: 2026-08-03 02:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20260803_001'
down_revision: Union[str, None] = '7736931efb06'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'container_image_transfer'")


def downgrade() -> None:
    pass  # PostgreSQL cannot drop enum values
