"""add_policy_baseline_drift_alert

Revision ID: 255828ad9c81
Revises: e4a56443300c
Create Date: 2026-05-14 03:02:03.682565

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '255828ad9c81'
down_revision: Union[str, None] = 'e4a56443300c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add template and risk_context columns to projects table
    op.add_column('projects', sa.Column('template', sa.String(64), nullable=True))
    op.add_column('projects', sa.Column('risk_context', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('projects', 'risk_context')
    op.drop_column('projects', 'template')
