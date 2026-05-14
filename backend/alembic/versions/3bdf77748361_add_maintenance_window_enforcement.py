"""add_maintenance_window_enforcement

Revision ID: 3bdf77748361
Revises: 743e53a51d75
Create Date: 2026-05-14 02:01:14.969668

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '3bdf77748361'
down_revision: Union[str, None] = '743e53a51d75'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('maintenance_window', sa.Column('enforcement', sa.String(16), server_default='advisory', nullable=False))


def downgrade() -> None:
    op.drop_column('maintenance_window', 'enforcement')
