"""add_runbook_change_types_to_pg_enum

Revision ID: 6c5d4e3f2a1b
Revises: 5b4ac4aea4ac
Create Date: 2026-05-18 05:00:00.000000

"""
from typing import Sequence, Union
from alembic import op

revision: str = '6c5d4e3f2a1b'
down_revision: Union[str, None] = '5b4ac4aea4ac'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_CHANGE_TYPES = [
    'create_ad_account',
    'assign_okta_groups',
    'add_github_org_member',
    'send_welcome_email',
    'force_password_reset',
    'preserve_cloudtrail_logs',
    'close_incident_ticket',
    'check_fleet_health',
    'check_compliance',
]


def upgrade() -> None:
    for value in NEW_CHANGE_TYPES:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    pass
