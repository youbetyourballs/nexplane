"""add_campaign_failure_policy

Revision ID: 45c660c90e38
Revises: 7702acf5e7b9
Create Date: 2026-05-14 02:22:57.183295

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '45c660c90e38'
down_revision: Union[str, None] = '7702acf5e7b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('patch_campaigns', sa.Column('failure_policy', sa.String(16), nullable=False, server_default='continue'))
    op.add_column('patch_campaigns', sa.Column('failed_cr_count', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('patch_campaigns', sa.Column('succeeded_cr_count', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    op.drop_column('patch_campaigns', 'succeeded_cr_count')
    op.drop_column('patch_campaigns', 'failed_cr_count')
    op.drop_column('patch_campaigns', 'failure_policy')
