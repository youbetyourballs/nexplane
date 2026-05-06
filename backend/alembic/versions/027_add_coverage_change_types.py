"""add coverage change types (block_s3_public_access, capture_instance_state)

Revision ID: 027
Revises: 026
Create Date: 2026-05-05
"""
from alembic import op

revision = '027'
down_revision = '026'
branch_labels = None
depends_on = None


def upgrade():
    new_types = [
        'block_s3_public_access',
        'restore_s3_public_access',
        'capture_instance_state',
    ]
    for t in new_types:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{t}'")


def downgrade():
    pass
