"""add_cr_snapshot_before

Revision ID: 72974d9404a9
Revises: 3bdf77748361
Create Date: 2026-05-14 02:06:30.341521

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '72974d9404a9'
down_revision: Union[str, None] = '3bdf77748361'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'change_requests',
        sa.Column('snapshot_before', sa.Boolean(), nullable=False, server_default='false'),
    )


def downgrade() -> None:
    op.drop_column('change_requests', 'snapshot_before')
