"""add vuln_poc_validate to change_type enum

Revision ID: 059_vuln_poc_validate
Revises: 058_merge_all_heads
Create Date: 2026-05-25
"""
from alembic import op

revision = "059_vuln_poc_validate"
down_revision = "058_merge_all_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'vuln_poc_validate'")


def downgrade() -> None:
    pass
