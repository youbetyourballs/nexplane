"""add ansible_local connector and change type

Revision ID: 022
Revises: 021
Create Date: 2026-05-04
"""
from alembic import op

revision = '022'
down_revision = '021'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE connector_type ADD VALUE IF NOT EXISTS 'ansible_local'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'ansible_local_playbook'")


def downgrade():
    pass
