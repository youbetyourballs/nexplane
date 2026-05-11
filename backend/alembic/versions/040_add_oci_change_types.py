"""add oci change_type values

Revision ID: 040
Revises: 039
Create Date: 2026-05-10
"""
from alembic import op

revision = '040'
down_revision = '039'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_instance_create'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_instance_stop'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_instance_start'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_instance_reboot'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_instance_delete'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_block_volume_snapshot'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_vcn_create'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_subnet_create'")


def downgrade() -> None:
    pass
