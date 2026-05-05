"""add azure vm lifecycle change types

Revision ID: 025
Revises: 024
Create Date: 2026-05-05
"""
from alembic import op

revision = '025'
down_revision = '024'
branch_labels = None
depends_on = None


def upgrade():
    new_types = [
        'azure_vm_create', 'azure_vm_stop', 'azure_vm_start',
        'azure_vm_reboot', 'azure_vm_delete', 'azure_vm_snapshot',
        'azure_run_command',
    ]
    for t in new_types:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{t}'")


def downgrade():
    pass
