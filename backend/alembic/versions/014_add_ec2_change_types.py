"""add ec2 instance lifecycle change types

Revision ID: 014
Revises: 013
Create Date: 2026-05-02
"""
from alembic import op

revision = '014'
down_revision = '013'
branch_labels = None
depends_on = None


def upgrade():
    for val in ['ec2_stop', 'ec2_start', 'ec2_reboot', 'ec2_stop_start', 'ec2_launch', 'ec2_terminate']:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{val}'")


def downgrade():
    pass
