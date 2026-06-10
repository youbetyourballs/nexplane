"""add macOS posture, hardening, and observability CR types

Revision ID: m280g1h4i5j6
Revises: k160f0g2h3i4
Create Date: 2026-06-10
"""
from alembic import op

revision = "m280g1h4i5j6"
down_revision = "k160f0g2h3i4"
branch_labels = None
depends_on = None

NEW_VALUES = [
    "apply_sysctl_hardening",
    "deploy_auditd_rules",
    "setup_file_integrity_monitoring",
    "discover_applications",
    "deep_discover",
    "collect_forensics",
    "harden_ssh",
    "configure_ntp",
    "config_syslog",
    "estimate_size",
    "audit_software_inventory",
    "audit_cis_compliance",
]


def upgrade() -> None:
    for v in NEW_VALUES:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{v}'")


def downgrade() -> None:
    pass
