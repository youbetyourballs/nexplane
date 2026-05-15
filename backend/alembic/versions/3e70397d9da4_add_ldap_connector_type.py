"""add_ldap_connector_type

Revision ID: 3e70397d9da4
Revises: 63208d3de341
Create Date: 2026-05-15 01:29:20.881287

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '3e70397d9da4'
down_revision: Union[str, None] = '63208d3de341'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE connector_type ADD VALUE IF NOT EXISTS 'ldap'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; downgrade is a no-op
    pass
