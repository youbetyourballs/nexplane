"""add mitigation cr types

Revision ID: 060_add_mitigation_cr_types
Revises: 059_vuln_poc_validate
Create Date: 2026-05-26
"""
from typing import Union
from alembic import op

revision: str = '060_add_mitigation_cr_types'
down_revision: Union[str, None] = '059_vuln_poc_validate'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'apply_protocol_control'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'disable_kernel_feature'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'apply_registry_fix'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'remove_vulnerable_package'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'revoke_exposed_credential'")


def downgrade() -> None:
    pass
