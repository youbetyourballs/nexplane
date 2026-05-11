"""add OCI storage change types

Revision ID: 041
Revises: 040
Create Date: 2026-05-10
"""
from alembic import op

revision = '041'
down_revision = '040'
branch_labels = None
depends_on = None


def upgrade():
    for t in [
        'oci_bucket_create',
        'oci_bucket_delete',
        'oci_bucket_lifecycle_set',
        'oci_bucket_block_public',
        'oci_block_volume_create',
        'oci_block_volume_attach',
        'oci_block_volume_detach',
        'oci_block_volume_delete',
        'oci_block_volume_backup',
    ]:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{t}'")


def downgrade():
    pass
