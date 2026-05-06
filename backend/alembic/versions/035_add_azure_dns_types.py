"""add Azure DNS change types

Revision ID: 035
Revises: 034
Create Date: 2026-05-06
"""
from alembic import op

revision = '035'
down_revision = '034'
branch_labels = None
depends_on = None


def upgrade():
    for t in ['azure_dns_zone_create', 'azure_dns_zone_delete',
              'azure_dns_record_create', 'azure_dns_record_delete']:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{t}'")


def downgrade():
    pass
