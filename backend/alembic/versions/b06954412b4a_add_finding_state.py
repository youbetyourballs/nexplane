"""add_finding_state

Revision ID: b06954412b4a
Revises: 72974d9404a9
Create Date: 2026-05-14 02:08:44.889197

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b06954412b4a'
down_revision: Union[str, None] = '72974d9404a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'vulnerability_findings',
        sa.Column('remediated_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'vulnerability_findings',
        sa.Column('remediated_by_cr_id', postgresql.UUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('vulnerability_findings', 'remediated_by_cr_id')
    op.drop_column('vulnerability_findings', 'remediated_at')
