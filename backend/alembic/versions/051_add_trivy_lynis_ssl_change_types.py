"""add trivy_scan lynis_audit ssl_cert_inspect change types

Revision ID: 051_trivy_lynis_ssl
Revises: 050_windows_hardening
Create Date: 2026-05-15 03:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '051_trivy_lynis_ssl'
down_revision: Union[str, None] = '050_windows_hardening'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_CHANGE_TYPES = [
    "trivy_scan",
    "lynis_audit",
    "ssl_cert_inspect",
    # Related security audit types that may also be missing
    "authorized_keys_audit",
    "sudoers_audit",
    "suid_scan",
    "openscap_scan",
]


def upgrade() -> None:
    for val in _NEW_CHANGE_TYPES:
        op.execute(sa.text(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{val}'"))


def downgrade() -> None:
    # PostgreSQL does not support removing enum values — downgrade is a no-op.
    pass
