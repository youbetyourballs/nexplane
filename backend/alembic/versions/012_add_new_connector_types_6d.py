"""add IaC connector types: terraform, ansible, cloudformation, pulumi, helm, bicep, checkov, saltstack, chef_inspec

Revision ID: 012
Revises: 011
Create Date: 2026-05-01
"""
from alembic import op

revision = '012'
down_revision = '011'
branch_labels = None
depends_on = None


def upgrade():
    for val in ['terraform', 'ansible', 'cloudformation', 'pulumi', 'helm', 'bicep', 'checkov', 'saltstack', 'chef_inspec']:
        op.execute(f"ALTER TYPE connector_type ADD VALUE IF NOT EXISTS '{val}'")


def downgrade():
    pass
