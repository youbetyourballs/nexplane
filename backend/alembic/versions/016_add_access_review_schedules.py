"""add_access_review_schedules

Revision ID: 016
Revises: 015
Create Date: 2026-05-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '016'
down_revision: Union[str, None] = '015'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'access_review_schedules',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('frequency_days', sa.Integer(), nullable=False),
        sa.Column('scope', sa.String(255), nullable=False, server_default='all_users'),
        sa.Column('reviewer_assignment_rule', sa.String(100), nullable=False, server_default='direct_manager'),
        sa.Column('last_review_created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('access_review_schedules')
