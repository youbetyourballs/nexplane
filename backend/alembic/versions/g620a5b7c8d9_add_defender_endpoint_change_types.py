"""add defender_endpoint change_types

Revision ID: g620a5b7c8d9
Revises: f510f4f16763
Create Date: 2026-05-27

"""
from typing import Union
from alembic import op

revision: str = 'g620a5b7c8d9'
down_revision: Union[str, None] = 'f510f4f16763'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'discover_machines'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'isolate_machine'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'unisolate_machine'")


def downgrade() -> None:
    pass
