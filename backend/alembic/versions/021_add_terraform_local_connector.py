"""add terraform_local connector and change type

Revision ID: 021
Revises: 020
Create Date: 2026-05-04
"""
from alembic import op

revision = '021'
down_revision = '020'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE connector_type ADD VALUE IF NOT EXISTS 'terraform_local'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'terraform_local_apply'")


def downgrade():
    pass
