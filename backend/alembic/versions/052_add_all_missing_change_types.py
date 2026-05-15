"""add_all_missing_change_types

Catches all change_type enum values present in the Python model (ChangeType)
but not yet in the PostgreSQL enum.  Uses IF NOT EXISTS so it is safe to
re-run and harmless for values already present.

Revision ID: 052_all_missing_change_types
Revises: 051_trivy_lynis_ssl
Create Date: 2026-05-15 03:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '052_all_missing_change_types'
down_revision: Union[str, None] = '051_wazuh_falco_infisical'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# All values that appear in app/models/change_request.py::ChangeType but were
# never added to the DB enum via a migration.
_NEW_CHANGE_TYPES = [
    # K8s (added with K8S_RBAC phase)
    "k8s_audit_rbac",
    "k8s_revoke_rolebinding",
    # Elastic Security
    "elastic_create_rule",
    "elastic_sync_alerts",
    # Falco
    "falco_policy_update",
    # FreeIPA
    "freeipa_disable_user",
    # Generic
    "generic_remediation",
    "notify_only",
    "safety_review",
    "suppress",
    # Gitea
    "gitea_suspend_user",
    # GitLab
    "gitlab_rotate_token",
    "gitlab_suspend_user",
    # IAM
    "iam_enforce_mfa",
    # JFrog
    "jfrog_scan_artifact",
    "jfrog_sync_violations",
    # Nessus
    "nessus_run_scan",
    # Okta
    "okta_disable_user",
    "okta_enforce_mfa",
    "okta_sync_users",
    # OpenVAS
    "openvas_import_findings",
    "openvas_run_scan",
    # OPNsense
    "opnsense_block_host",
    "opnsense_update_rule",
    # PagerDuty
    "pagerduty_create_incident",
    "pagerduty_resolve_incident",
    # Database rotation (new connectors)
    "rotate_infisical_secret",
    "rotate_mongodb_password",
    "rotate_postgres_password",
    "rotate_redis_password",
    # ServiceNow
    "servicenow_close_incident",
    "servicenow_create_incident",
    # Snyk
    "snyk_scan_image",
    "snyk_sync_findings",
    # Splunk
    "splunk_create_alert",
    "splunk_sync_notables",
    # Step CA
    "step_ca_check_expiry",
    "step_ca_rotate_cert",
    # Teleport
    "teleport_lock_user",
    # Wazuh
    "wazuh_deploy_agent",
    # Route53 / DR (in case not present)
    "dr_dns_failover_route53",
    # Block S3 / restore (in case not present)
    "block_s3_public_access",
    "restore_s3_public_access",
]


def upgrade() -> None:
    for val in _NEW_CHANGE_TYPES:
        op.execute(sa.text(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{val}'"))


def downgrade() -> None:
    # PostgreSQL does not support removing enum values — downgrade is a no-op.
    pass
