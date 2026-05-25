"""merge all heads: vuln+tokens+santa_sync_server

Revision ID: 058_merge_all_heads
Revises: 057_merge_heads, c3d4e5f6a7b8
Create Date: 2026-05-25
"""
from alembic import op

revision = "058_merge_all_heads"
down_revision = ("057_merge_heads", "c3d4e5f6a7b8")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
