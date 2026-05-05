"""add gce instance lifecycle change types

Revision ID: 024
Revises: 023
Create Date: 2026-05-05
"""
from alembic import op

revision = '024'
down_revision = '023'
branch_labels = None
depends_on = None


def upgrade():
    new_types = [
        'gce_instance_create', 'gce_stop', 'gce_start',
        'gce_instance_reboot', 'gce_instance_delete', 'gce_disk_snapshot',
    ]
    for t in new_types:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{t}'")


def downgrade():
    pass
