"""Add OCI networking change types (security list, NSG, load balancer, DNS)

Revision ID: 042
Revises: 041
Create Date: 2026-05-10
"""
from alembic import op

revision = '042'
down_revision = '041'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_security_list_add_rule'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_security_list_remove_rule'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_nsg_create'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_nsg_delete'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_nsg_rule_add'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_nsg_rule_remove'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_load_balancer_create'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_load_balancer_delete'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_backend_set_create'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_listener_create'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_dns_zone_create'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_dns_record_upsert'")


def downgrade() -> None:
    pass  # PostgreSQL does not support removing enum values
