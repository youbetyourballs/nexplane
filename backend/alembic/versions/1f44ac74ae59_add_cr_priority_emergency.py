"""add_cr_priority_emergency

Revision ID: 1f44ac74ae59
Revises: 45c660c90e38
Create Date: 2026-05-14 02:25:45.136450

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '1f44ac74ae59'
down_revision: Union[str, None] = '45c660c90e38'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ChangeRequest priority fields
    op.add_column('change_requests', sa.Column('priority', sa.String(length=16), nullable=False, server_default='normal'))
    op.add_column('change_requests', sa.Column('emergency_reason', sa.Text(), nullable=True))
    # OrgSettings escalation fields
    op.add_column('organization_settings', sa.Column('escalation_chain', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('organization_settings', sa.Column('escalation_timeout_minutes', sa.Integer(), nullable=False, server_default='30'))


def downgrade() -> None:
    op.drop_column('organization_settings', 'escalation_timeout_minutes')
    op.drop_column('organization_settings', 'escalation_chain')
    op.drop_column('change_requests', 'emergency_reason')
    op.drop_column('change_requests', 'priority')
