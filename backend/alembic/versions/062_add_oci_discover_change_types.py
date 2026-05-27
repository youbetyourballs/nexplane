"""add oci_discover and azure_ad change_types to pg enum

Revision ID: 062_add_oci_discover_change_types
Revises: 061_agent_tokens
Create Date: 2026-05-27
"""
from alembic import op


revision = "062_add_oci_discover_change_types"
down_revision = "061_agent_tokens"
branch_labels = None
depends_on = None


def upgrade():
    for val in [
        "oci_discover_compartments",
        "oci_discover_instances",
        "oci_discover_vcns",
        "discover_users",
        "get_group_membership",
    ]:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{val}'")


def downgrade():
    pass
