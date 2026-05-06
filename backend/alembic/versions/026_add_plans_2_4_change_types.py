"""add Plans 2-4 change types (IAM, S3, DR, RDS, GCP, Azure ops)

Revision ID: 026
Revises: 025
Create Date: 2026-05-05
"""
from alembic import op

revision = '026'
down_revision = '025'
branch_labels = None
depends_on = None


def upgrade():
    new_types = [
        # AWS Plans 2 — IAM, S3, tagging, agent, DR, RDS
        'attach_iam_policy', 'detach_iam_policy', 'disable_iam_user', 'enable_iam_user',
        'rotate_iam_key', 'put_bucket_policy', 'tag_resource', 'remove_nexplane_agent',
        'dr_dns_failover_route53', 'rds_replica_create',
        # GCP Plan 3 — firewall, storage, service accounts
        'gcp_firewall_create', 'gcp_firewall_delete', 'gcp_block_public_bucket_access',
        'gcp_disable_service_account', 'gcp_rotate_service_account_key',
        # Azure Plan 4 — NSG, blob storage, storage key
        'azure_update_nsg_rule', 'azure_restore_nsg_rule',
        'azure_disable_public_blob_access', 'azure_enable_public_blob_access',
        'azure_rotate_storage_key',
    ]
    for t in new_types:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{t}'")


def downgrade():
    pass
