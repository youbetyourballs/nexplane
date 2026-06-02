"""add ebpf_network and ebpf_lsm policy types

Revision ID: 068_ebpf_policy_types
Revises: 067_merge_rollback
Create Date: 2026-06-01
"""
from alembic import op

revision = "068_ebpf_policy_types"
down_revision = "067_merge_rollback"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'ebpf_network_soak'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'configure_ebpf_network'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'configure_ebpf_lsm'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'promote_ebpf_policy'")

    op.execute("""
        ALTER TABLE security_policy_soak_sessions
          DROP CONSTRAINT IF EXISTS security_policy_soak_sessions_policy_type_check,
          DROP CONSTRAINT IF EXISTS ck_soak_sessions_policy_type,
          ADD CONSTRAINT security_policy_soak_sessions_policy_type_check
            CHECK (policy_type IN ('seccomp','apparmor','selinux','ebpf_network','ebpf_lsm'))
    """)
    op.execute("""
        ALTER TABLE security_policy_baselines
          DROP CONSTRAINT IF EXISTS security_policy_baselines_policy_type_check,
          ADD CONSTRAINT security_policy_baselines_policy_type_check
            CHECK (policy_type IN ('seccomp','apparmor','selinux','ebpf_network','ebpf_lsm'))
    """)


def downgrade():
    pass  # Postgres enum values cannot be removed without recreation
