"""add_route53_dnssec_enable_and_aws_account_full_baseline_change_types

Revision ID: sdu008
Revises: sdu007
Create Date: 2026-08-06 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'sdu008'
down_revision: Union[str, None] = 'sdu007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'route53_dnssec_enable'")
        op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'aws_account_full_baseline'")


def downgrade() -> None:
    pass  # PostgreSQL cannot drop enum values
