# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

# PREREQUISITE: Before smoke testing, the user must complete the following manual steps:
# 1. Create a Cloud Identity Free account and link it to their domain (e.g. nexplane.ai)
#    at https://workspace.google.com/products/cloud-identity/
# 2. Move the GCP project under the resulting organization node in the Resource Manager
# 3. Grant the Nexplane service account roles/securitycenter.admin at the org level:
#    gcloud organizations add-iam-policy-binding ORG_ID \
#      --member="serviceAccount:SA_EMAIL" \
#      --role="roles/securitycenter.admin"
# These steps cannot be automated — they require a human with domain-owner + org-admin access.

import json
import logging

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "SCC cannot be disabled once enabled at org level"


def _scc_client(creds: dict):
    from google.cloud import securitycenter
    from google.oauth2 import service_account

    sa_json = creds.get("service_account_json") or creds.get("service_account_key_json")
    if sa_json:
        sa_info = json.loads(sa_json) if isinstance(sa_json, str) else sa_json
        credentials = service_account.Credentials.from_service_account_info(
            sa_info, scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        return securitycenter.SecurityCenterClient(credentials=credentials)
    return securitycenter.SecurityCenterClient()


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    org_id = parameters.get("org_id", "").strip()
    if not org_id:
        raise ValueError("enable_security_command_center: org_id is required")

    tier = parameters.get("tier", "STANDARD").upper()
    if tier not in ("STANDARD", "PREMIUM"):
        raise ValueError(f"enable_security_command_center: tier must be STANDARD or PREMIUM, got {tier!r}")

    creds = connector.credentials if hasattr(connector, "credentials") else {}

    client = _scc_client(creds)
    org_name = f"organizations/{org_id}"

    logger.info("enable_security_command_center: fetching org settings for %s", org_name)
    settings = client.get_organization_settings(name=f"{org_name}/organizationSettings")

    settings.enable_asset_discovery = True
    update_mask = {"paths": ["enable_asset_discovery"]}

    logger.info("enable_security_command_center: updating org settings (enable_asset_discovery=True)")
    client.update_organization_settings(
        organization_settings=settings,
        update_mask=update_mask,
    )

    logger.info("enable_security_command_center: SCC enabled for org %s (tier=%s)", org_id, tier)
    return {
        "org_id": org_id,
        "tier": tier,
        "asset_discovery_enabled": True,
        "status": "enabled",
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": False,
        "reason": ROLLBACK_REASON,
        "org_id": execution_result.get("org_id"),
    }
