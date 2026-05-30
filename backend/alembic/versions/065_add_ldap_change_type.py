"""add ldap_disable_user change_type to pg enum

Revision ID: 065_ldap_change_type
Revises: 064_backup_recovery
Create Date: 2026-05-30
"""
from alembic import op


revision = "065_ldap_change_type"
down_revision = "064_backup_recovery"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'ldap_disable_user'")


def downgrade():
    pass
