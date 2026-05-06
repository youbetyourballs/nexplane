"""add sla_config to org_settings

Revision ID: 030
Revises: 029
Create Date: 2026-05-05
"""
from alembic import op
import sqlalchemy as sa

revision = '030'
down_revision = '029'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('organization_settings', sa.Column('sla_config', sa.JSON(), nullable=True))


def downgrade():
    op.drop_column('organization_settings', 'sla_config')
