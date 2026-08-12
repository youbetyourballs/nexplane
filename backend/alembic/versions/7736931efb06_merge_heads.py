"""merge_heads

Revision ID: 7736931efb06
Revises: 56adf7a75507, ecs001
Create Date: 2026-08-03 02:24:14.111525

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7736931efb06'
down_revision: Union[str, None] = ('56adf7a75507', 'ecs001')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
