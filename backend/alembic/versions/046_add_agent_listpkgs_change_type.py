"""add agent_listpkgs change type

Revision ID: 046
Revises: 045
Create Date: 2026-05-11
"""
from alembic import op

revision = '046'
down_revision = '045'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'agent_listpkgs'")


def downgrade():
    pass
