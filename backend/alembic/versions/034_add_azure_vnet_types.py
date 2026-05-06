"""add Azure VNet change types

Revision ID: 034
Revises: 033
Create Date: 2026-05-06
"""
from alembic import op

revision = '034'
down_revision = '033'
branch_labels = None
depends_on = None


def upgrade():
    for t in ['azure_vnet_create', 'azure_vnet_delete']:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{t}'")


def downgrade():
    pass
