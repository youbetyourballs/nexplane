"""add aws expansion change types

Revision ID: 023
Revises: 022
Create Date: 2026-05-04
"""
from alembic import op

revision = '023'
down_revision = '022'
branch_labels = None
depends_on = None


def upgrade():
    new_types = [
        'iam_user_create', 'iam_user_delete',
        's3_bucket_create', 's3_bucket_delete', 's3_lifecycle_configure',
        'route53_zone_create', 'route53_record_upsert', 'route53_record_delete',
        'rds_instance_create', 'rds_instance_delete', 'rds_snapshot_create',
        'cloudwatch_alarm_create', 'cloudwatch_alarm_delete',
    ]
    for t in new_types:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{t}'")


def downgrade():
    pass
