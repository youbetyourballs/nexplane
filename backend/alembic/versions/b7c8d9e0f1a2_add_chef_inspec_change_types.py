"""add_chef_inspec_change_types

Revision ID: b7c8d9e0f1a2
Revises: 061_agent_tokens
Create Date: 2026-05-27 03:55:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b7c8d9e0f1a2'
down_revision: Union[str, None] = '061_agent_tokens'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'discover_nodes'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'run_compliance_scan'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'discover_compliance_results'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values
    pass
