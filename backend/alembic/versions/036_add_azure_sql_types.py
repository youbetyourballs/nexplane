"""add Azure SQL change types

Revision ID: 036
Revises: 035
Create Date: 2026-05-06
"""
from alembic import op

revision = '036'
down_revision = '035'
branch_labels = None
depends_on = None


def upgrade():
    for t in ['azure_sql_server_create', 'azure_sql_server_delete',
              'azure_sql_database_create', 'azure_sql_database_delete']:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{t}'")


def downgrade():
    pass
