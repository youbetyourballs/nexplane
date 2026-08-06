"""add freeipa_upgrade change type

Revision ID: iau002
Revises: iau001
Create Date: 2026-08-05
"""
from alembic import op

revision = 'iau002'
down_revision = 'iau001'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'freeipa_upgrade'")


def downgrade():
    pass
