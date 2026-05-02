"""add new connector types for 6c: sentinelone, defender_endpoint, hashicorp_vault, github, kubernetes, snyk, qualys

Revision ID: 011
Revises: 010
Create Date: 2026-05-01
"""
from alembic import op

revision = '011'
down_revision = '010'
branch_labels = None
depends_on = None


def upgrade():
    for val in ['sentinelone', 'defender_endpoint', 'hashicorp_vault', 'github', 'kubernetes', 'snyk', 'qualys']:
        op.execute(f"ALTER TYPE connector_type ADD VALUE IF NOT EXISTS '{val}'")


def downgrade():
    pass
