"""add_azure_ad_connector_type_and_change_types

Revision ID: 63208d3de341
Revises: d2e3f4a5b6c7
Create Date: 2026-05-14 06:31:12.788353

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '63208d3de341'
down_revision: Union[str, None] = 'd2e3f4a5b6c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add azure_ad to connector_type enum
    op.execute("ALTER TYPE connector_type ADD VALUE IF NOT EXISTS 'azure_ad'")
    # Add new change types for Azure AD identity operations
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'azure_ad_disable_user'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'azure_ad_create_user'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; downgrade is a no-op.
    pass
