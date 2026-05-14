"""add_cr_verification_checks

Revision ID: 09cf15c952c9
Revises: b06954412b4a
Create Date: 2026-05-14 02:14:12.961761

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '09cf15c952c9'
down_revision: Union[str, None] = 'b06954412b4a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'change_requests',
        sa.Column(
            'verification_checks',
            postgresql.JSONB(astext_type=sa.Text()),
            server_default='[]',
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column('change_requests', 'verification_checks')
