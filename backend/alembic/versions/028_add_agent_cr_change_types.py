"""add agent CR change types

Revision ID: 028
Revises: 027
Create Date: 2026-05-05
"""
from alembic import op

revision = '028'
down_revision = '027'
branch_labels = None
depends_on = None


def upgrade():
    new_types = [
        'agent_linux_patch', 'agent_ossecurity', 'agent_linuxauth',
        'agent_crossplatform', 'agent_compliance', 'agent_forensics',
        'agent_fleet', 'agent_backup', 'agent_reboot', 'agent_credrotation',
        'agent_iac', 'agent_linuxupgrade', 'agent_win_patch', 'agent_winharden',
    ]
    for t in new_types:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{t}'")


def downgrade():
    pass
