"""add ip migration change types

Revision ID: 038
Revises: 037
Create Date: 2026-05-09
"""
from alembic import op

revision = '038'
down_revision = '037'
branch_labels = None
depends_on = None


def upgrade():
    for t in ['change_ip', 'migrate_ip', 'ip_campaign']:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{t}'")


def downgrade():
    pass
