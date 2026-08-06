"""add openldap_schema_migration change type

Revision ID: iau003
Revises: iau002
Create Date: 2026-08-05
"""
from alembic import op

revision = 'iau003'
down_revision = 'iau002'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'openldap_schema_migration'")


def downgrade():
    pass
