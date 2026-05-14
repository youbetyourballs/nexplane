"""add_cr_batch_id

Revision ID: 7702acf5e7b9
Revises: 09cf15c952c9
Create Date: 2026-05-14 02:20:23.808931

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '7702acf5e7b9'
down_revision: Union[str, None] = '09cf15c952c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('change_requests', sa.Column('batch_id', sa.UUID(), nullable=True))
    op.create_index(op.f('ix_change_requests_batch_id'), 'change_requests', ['batch_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_change_requests_batch_id'), table_name='change_requests')
    op.drop_column('change_requests', 'batch_id')
