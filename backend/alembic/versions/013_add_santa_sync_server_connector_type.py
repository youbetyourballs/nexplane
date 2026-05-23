"""add santa_sync_server connector type and macos_fleet asset type

Revision ID: 013
Revises: f510f4f16763
Create Date: 2026-05-23
"""
from alembic import op

revision = 'a9b8c7d6e5f4'
down_revision = 'f510f4f16763'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE connector_type ADD VALUE IF NOT EXISTS 'santa_sync_server'")
    op.execute("ALTER TYPE asset_type ADD VALUE IF NOT EXISTS 'macos_fleet'")
    for val in ['santa_policy_audit', 'santa_push_rules', 'santa_machine_list', 'santa_machine_group_assign', 'santa_rule_deploy']:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{val}'")


def downgrade():
    pass
