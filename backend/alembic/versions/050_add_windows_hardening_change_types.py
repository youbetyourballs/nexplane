"""add_windows_hardening_change_types

Revision ID: 050_windows_hardening
Revises: 184f1eea3cad
Create Date: 2026-05-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '050_windows_hardening'
down_revision: Union[str, None] = '049'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_CHANGE_TYPES = [
    "configure_windows_firewall",
    "deploy_applocker_policy",
    "configure_windows_audit_policy",
    "wdac_audit",
    "wdac_enforce",
    "asr_audit",
    "asr_enforce",
    "sysmon_deploy",
    "sysmon_fim",
]


def upgrade() -> None:
    for val in _NEW_CHANGE_TYPES:
        op.execute(sa.text(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{val}'"))


def downgrade() -> None:
    # Postgres does not support removing enum values — downgrade is a no-op.
    pass
