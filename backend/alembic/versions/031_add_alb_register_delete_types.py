"""add ALB register/delete change types

Revision ID: 031
Revises: 030
Create Date: 2026-05-06
"""
from alembic import op

revision = '031'
down_revision = '030'
branch_labels = None
depends_on = None


def upgrade():
    new_types = [
        'target_group_delete',
        'listener_delete',
        'register_targets',
        'deregister_targets',
    ]
    for t in new_types:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{t}'")


def downgrade():
    pass
