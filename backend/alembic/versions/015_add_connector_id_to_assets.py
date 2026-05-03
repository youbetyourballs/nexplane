"""add connector_id to assets for multi-account scoping

Revision ID: 015
Revises: 014
Create Date: 2026-05-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = '015'
down_revision = '014'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('assets', sa.Column(
        'connector_id',
        UUID(as_uuid=True),
        sa.ForeignKey('connectors.id', ondelete='SET NULL'),
        nullable=True,
    ))
    op.create_index('ix_assets_connector_id', 'assets', ['connector_id'])


def downgrade():
    op.drop_index('ix_assets_connector_id', table_name='assets')
    op.drop_column('assets', 'connector_id')
