"""add Azure IAM change types

Revision ID: 033
Revises: 032
Create Date: 2026-05-06
"""
from alembic import op

revision = '033'
down_revision = '032'
branch_labels = None
depends_on = None


def upgrade():
    new_types = [
        'azure_managed_identity_create', 'azure_managed_identity_delete',
        'azure_role_assignment_create', 'azure_role_assignment_delete',
    ]
    for t in new_types:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{t}'")


def downgrade():
    pass
