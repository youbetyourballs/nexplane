"""add_smtp_connector_type

Revision ID: 5b4ac4aea4ac
Revises: c9d8e7f6a5b4
Create Date: 2026-05-18 04:19:01.907021

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '5b4ac4aea4ac'
down_revision: Union[str, None] = 'c9d8e7f6a5b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE connector_type ADD VALUE IF NOT EXISTS 'smtp'")


def downgrade() -> None:
    pass
