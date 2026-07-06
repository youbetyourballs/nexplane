# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Add change_type enum values for GCP phases S-X (bucket, SA/IAM, DNS, SQL, monitoring)

Revision ID: gcp001
Revises: cwr001
Create Date: 2026-07-06
"""

from alembic import op

revision = "gcp001"
down_revision = "cwr001"
branch_labels = None
depends_on = None


def upgrade():
    new_types = [
        # Phase T — GCS bucket lifecycle
        "gcp_bucket_create",
        "gcp_bucket_delete",
        # Phase U — Service account + IAM binding
        "gcp_service_account_create",
        "gcp_service_account_delete",
        "gcp_iam_binding_add",
        "gcp_iam_binding_remove",
        # Phase V — Cloud DNS
        "gcp_dns_zone_create",
        "gcp_dns_zone_delete",
        "gcp_dns_record_create",
        "gcp_dns_record_delete",
        # Phase W — Cloud SQL
        "gcp_cloudsql_instance_create",
        "gcp_cloudsql_instance_delete",
        "gcp_cloudsql_backup_create",
        # Phase X — Cloud Monitoring
        "gcp_alert_policy_create",
        "gcp_alert_policy_delete",
        "gcp_uptime_check_create",
        "gcp_uptime_check_delete",
    ]
    for t in new_types:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{t}'")


def downgrade():
    # PostgreSQL does not support removing enum values; downgrade is a no-op
    pass
