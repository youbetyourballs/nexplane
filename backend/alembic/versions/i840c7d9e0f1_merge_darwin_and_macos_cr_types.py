"""merge darwin os_type and macos cr_types heads

Revision ID: i840c7d9e0f1
Revises: 069_add_darwin_os_type, h730b6c8d9e0
Create Date: 2026-06-03
"""
from alembic import op

revision = "i840c7d9e0f1"
down_revision = ("069_add_darwin_os_type", "h730b6c8d9e0")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
