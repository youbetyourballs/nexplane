"""add ALB lifecycle change types

Revision ID: 029
Revises: 028
Create Date: 2026-05-05
"""
from alembic import op

revision = '029'
down_revision = '028'
branch_labels = None
depends_on = None


def upgrade():
    new_types = [
        'alb_create', 'alb_delete',
        'target_group_create',
        'listener_create', 'listener_modify',
    ]
    for t in new_types:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{t}'")


def downgrade():
    pass
