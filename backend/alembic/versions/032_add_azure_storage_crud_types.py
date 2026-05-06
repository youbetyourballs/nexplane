"""add Azure storage CRUD change types

Revision ID: 032
Revises: 031
Create Date: 2026-05-06
"""
from alembic import op

revision = '032'
down_revision = '031'
branch_labels = None
depends_on = None


def upgrade():
    new_types = [
        'azure_storage_account_create', 'azure_storage_account_delete',
        'azure_blob_container_create', 'azure_blob_container_delete',
    ]
    for t in new_types:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{t}'")


def downgrade():
    pass
