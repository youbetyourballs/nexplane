"""add_winrm_sccm_change_types

Revision ID: a1b2c3d4e5f6
Revises: c862eba68975
Create Date: 2026-05-17 11:00:00.000000

"""
from typing import Sequence, Union
from alembic import op

revision: str = 'b3c4d5e6f7a8'
down_revision: Union[str, None] = '1d2e3eafddc7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_CHANGE_TYPES = [
    'winrm_check_prerequisites', 'winrm_download_agent', 'winrm_install_agent',
    'winrm_execute_template', 'winrm_collect_diagnostics',
    'sccm_deploy_application', 'sccm_run_script', 'sccm_collect_inventory',
    'sccm_trigger_client_action',
]


def upgrade() -> None:
    for val in NEW_CHANGE_TYPES:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{val}'")


def downgrade() -> None:
    pass
