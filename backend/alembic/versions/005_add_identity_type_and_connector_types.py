"""add identity asset type and new connector types

Revision ID: 005
Revises: 004
Create Date: 2026-04-27 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE asset_type ADD VALUE IF NOT EXISTS 'identity'")
    op.execute("ALTER TYPE connector_type ADD VALUE IF NOT EXISTS 'active_directory_mock'")
    op.execute("ALTER TYPE connector_type ADD VALUE IF NOT EXISTS 'crowdstrike_mock'")
    op.execute("ALTER TYPE connector_type ADD VALUE IF NOT EXISTS 'tenable_mock'")


def downgrade() -> None:
    # PostgreSQL cannot remove enum values — intentional no-op
    pass
