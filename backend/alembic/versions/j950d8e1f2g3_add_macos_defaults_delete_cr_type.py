"""add macos_defaults_delete to change_type enum

Revision ID: j950d8e1f2g3
Revises: i840c7d9e0f1
Create Date: 2026-06-04
"""
from alembic import op

revision = "j950d8e1f2g3"
down_revision = "i840c7d9e0f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'macos_defaults_delete'")


def downgrade() -> None:
    pass
