"""add workflow/observability connector types: jira, pagerduty, servicenow, splunk, datadog, zscaler, google_workspace

Revision ID: 013
Revises: 012
Create Date: 2026-05-01
"""
from alembic import op

revision = '013'
down_revision = '012'
branch_labels = None
depends_on = None


def upgrade():
    for val in ['jira', 'pagerduty', 'servicenow', 'splunk', 'datadog', 'zscaler', 'google_workspace']:
        op.execute(f"ALTER TYPE connector_type ADD VALUE IF NOT EXISTS '{val}'")


def downgrade():
    pass
