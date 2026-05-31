"""add_apparmor_selinux_change_types

Revision ID: f1dd971a6d60
Revises: 066_security_policy_soak
Create Date: 2026-05-31 00:28:14.403958

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f1dd971a6d60'
down_revision: Union[str, None] = '066_security_policy_soak'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'configure_apparmor'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'configure_selinux'")


def downgrade() -> None:
    pass  # Postgres enum values cannot be removed without recreation
