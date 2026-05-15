"""add_wazuh_falco_infisical_connector_types

Revision ID: 051_wazuh_falco_infisical
Revises: 050_windows_hardening
Create Date: 2026-05-15 03:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '051_wazuh_falco_infisical'
down_revision: Union[str, None] = '050_windows_hardening'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add new enum values to the PostgreSQL connector_type enum
    # PostgreSQL requires ALTER TYPE ... ADD VALUE for each new value
    op.execute("ALTER TYPE connector_type ADD VALUE IF NOT EXISTS 'wazuh'")
    op.execute("ALTER TYPE connector_type ADD VALUE IF NOT EXISTS 'falco'")
    op.execute("ALTER TYPE connector_type ADD VALUE IF NOT EXISTS 'infisical'")
    op.execute("ALTER TYPE connector_type ADD VALUE IF NOT EXISTS 'gitea'")


def downgrade() -> None:
    # PostgreSQL doesn't support removing enum values without recreating the type.
    # This downgrade is a no-op (values remain but are unused).
    pass
