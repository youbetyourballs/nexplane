"""add tailscale_generate_auth_key change type

Revision ID: o500i3j6k7l8
Revises: n390h2i5j6k7
Create Date: 2026-06-15
"""
from alembic import op

revision = "o500i3j6k7l8"
down_revision = "n390h2i5j6k7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'tailscale_generate_auth_key'")


def downgrade() -> None:
    pass
