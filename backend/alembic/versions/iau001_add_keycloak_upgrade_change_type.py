"""add keycloak_upgrade change type

Revision ID: iau001
Revises: wpm001
Create Date: 2026-08-05
"""
from alembic import op

revision = 'iau001'
down_revision = 'wpm001'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'keycloak_upgrade'")


def downgrade():
    pass
