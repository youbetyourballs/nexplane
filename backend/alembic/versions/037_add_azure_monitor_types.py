"""add Azure Monitor change types

Revision ID: 037
Revises: 036
Create Date: 2026-05-06
"""
from alembic import op

revision = '037'
down_revision = '036'
branch_labels = None
depends_on = None


def upgrade():
    for t in ['azure_metric_alert_create', 'azure_metric_alert_delete']:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{t}'")


def downgrade():
    pass
