"""merge vuln poc lifecycle and api tokens branches

Revision ID: 057_merge_heads
Revises: 055, 056_api_tokens
Create Date: 2026-05-25
"""
from alembic import op

revision = "057_merge_heads"
down_revision = ("055", "056_api_tokens")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
