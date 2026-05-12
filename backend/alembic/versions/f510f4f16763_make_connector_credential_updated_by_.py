"""make_connector_credential_updated_by_nullable

Revision ID: f510f4f16763
Revises: 044
Create Date: 2026-05-12 02:48:31.865676

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f510f4f16763'
down_revision: Union[str, None] = '044'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('connector_credentials', 'updated_by',
                    existing_type=sa.UUID(),
                    nullable=True)


def downgrade() -> None:
    op.alter_column('connector_credentials', 'updated_by',
                    existing_type=sa.UUID(),
                    nullable=False)
