"""add OCI identity change types

Revision ID: 043
Revises: 042
Create Date: 2026-05-10
"""
from alembic import op

revision = '043'
down_revision = '042'
branch_labels = None
depends_on = None

_NEW_TYPES = [
    'oci_iam_user_create',
    'oci_iam_user_delete',
    'oci_iam_user_disable',
    'oci_iam_user_enable',
    'oci_iam_group_create',
    'oci_iam_group_delete',
    'oci_iam_policy_create',
    'oci_iam_policy_delete',
    'oci_vault_secret_create',
    'oci_vault_secret_delete',
    'oci_compartment_create',
    'oci_compartment_delete',
]


def upgrade():
    for t in _NEW_TYPES:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{t}'")


def downgrade():
    pass
