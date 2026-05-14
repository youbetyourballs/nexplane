"""add_cr_finding_ids

Revision ID: 743e53a51d75
Revises: 165954e097c8
Create Date: 2026-05-14 02:00:01.319621

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '743e53a51d75'
down_revision: Union[str, None] = '165954e097c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('change_requests', sa.Column('finding_ids', sa.ARRAY(sa.String()), server_default='{}', nullable=False))


def downgrade() -> None:
    op.drop_column('change_requests', 'finding_ids')
