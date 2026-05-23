"""add santa sync server change types to change_type enum

Revision ID: c3d4e5f6a7b8
Revises: b7c8d9e0f1a2
Create Date: 2026-05-23
"""
from alembic import op

revision = 'c3d4e5f6a7b8'
down_revision = 'b7c8d9e0f1a2'
branch_labels = None
depends_on = None


def upgrade():
    for val in ['santa_policy_audit', 'santa_push_rules', 'santa_machine_list', 'santa_machine_group_assign', 'santa_rule_deploy']:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{val}'")


def downgrade():
    pass
