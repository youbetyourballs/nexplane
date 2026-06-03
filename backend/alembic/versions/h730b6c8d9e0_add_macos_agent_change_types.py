"""add macos agent change types

Revision ID: h730b6c8d9e0
Revises: g620a5b7c8d9
Create Date: 2026-06-03
"""
from alembic import op

revision = "h730b6c8d9e0"
down_revision = "g620a5b7c8d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    new_values = [
        "macos_profiles_install",
        "macos_profiles_remove",
        "macos_defaults_write",
        "macos_homebrew_list",
        "macos_santa_check",
        "macos_santa_rule_add",
        "macos_santa_rule_list",
        "macos_santa_rule_remove",
        "macos_santa_mode_set",
        "macos_santa_event_export",
        "macos_santa_binary_check",
        "macos_santa_sync_trigger",
        "macos_gatekeeper_status",
    ]
    for val in new_values:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{val}'")


def downgrade() -> None:
    pass
