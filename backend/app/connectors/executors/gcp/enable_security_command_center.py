# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""GCP Security Command Center enablement executor.

Enables SCC at the organization level. The executor auto-discovers the org
from the GCP project and attempts to self-grant roles/securitycenter.admin
at the org level if the SA lacks it.

One-time prerequisites (manual, outside Nexplane):
  1. Create a Cloud Identity Free account linked to your domain:
       https://workspace.google.com/products/cloud-identity/
  2. Move the GCP project under the resulting org:
       gcloud projects move PROJECT_ID --organization=ORG_ID
  After those two steps this executor handles the rest automatically.
"""

import json
import logging

import requests as _requests

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "SCC cannot be disabled once enabled at org level"

_CRM = "https://cloudresourcemanager.googleapis.com"
_SCC_ROLE = "roles/securitycenter.admin"


def _get_credentials(creds: dict):
    from google.oauth2 import service_account

    sa_json = creds.get("service_account_json") or creds.get("service_account_key_json")
    if sa_json:
        sa_info = json.loads(sa_json) if isinstance(sa_json, str) else sa_json
        return service_account.Credentials.from_service_account_info(
            sa_info, scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
    import google.auth
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    return credentials


def _token(credentials) -> str:
    import google.auth.transport.requests
    credentials.refresh(google.auth.transport.requests.Request())
    return credentials.token


def _sa_email(creds: dict) -> str:
    sa_json = creds.get("service_account_json") or creds.get("service_account_key_json")
    if sa_json:
        info = json.loads(sa_json) if isinstance(sa_json, str) else sa_json
        return info.get("client_email", "")
    return ""


def _project_id(creds: dict) -> str:
    sa_json = creds.get("service_account_json") or creds.get("service_account_key_json")
    if sa_json:
        info = json.loads(sa_json) if isinstance(sa_json, str) else sa_json
        return info.get("project_id", "")
    return creds.get("project_id", "")


def _discover_org_id(token: str, project_id: str) -> str | None:
    """Return org ID from the project's parent, or None if not under an org."""
    r = _requests.get(
        f"{_CRM}/v1/projects/{project_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    r.raise_for_status()
    parent = r.json().get("parent", {})
    if parent.get("type") == "organization":
        return parent["id"]
    return None


def _has_org_scc_role(token: str, org_id: str, sa_email: str) -> bool:
    """Return True if the SA already has roles/securitycenter.admin at org level."""
    r = _requests.post(
        f"{_CRM}/v1/organizations/{org_id}:getIamPolicy",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={},
        timeout=15,
    )
    if r.status_code == 403:
        return False
    r.raise_for_status()
    member = f"serviceAccount:{sa_email}"
    for binding in r.json().get("bindings", []):
        if binding.get("role") == _SCC_ROLE and member in binding.get("members", []):
            return True
    return False


def _grant_org_scc_role(token: str, org_id: str, sa_email: str) -> bool:
    """
    Add roles/securitycenter.admin to the SA at org level via read-modify-write.
    Returns True on success, False if the SA lacks permission to set org IAM.
    """
    # Read current policy
    r = _requests.post(
        f"{_CRM}/v1/organizations/{org_id}:getIamPolicy",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={},
        timeout=15,
    )
    if r.status_code == 403:
        return False
    r.raise_for_status()
    policy = r.json()

    member = f"serviceAccount:{sa_email}"
    # Find or create the binding
    bindings = policy.setdefault("bindings", [])
    for binding in bindings:
        if binding.get("role") == _SCC_ROLE:
            if member not in binding["members"]:
                binding["members"].append(member)
            break
    else:
        bindings.append({"role": _SCC_ROLE, "members": [member]})

    r2 = _requests.post(
        f"{_CRM}/v1/organizations/{org_id}:setIamPolicy",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"policy": policy},
        timeout=15,
    )
    if r2.status_code == 403:
        return False
    r2.raise_for_status()
    logger.info("enable_security_command_center: granted %s to %s on org %s", _SCC_ROLE, sa_email, org_id)
    return True


def _scc_client(credentials):
    from google.cloud import securitycenter
    return securitycenter.SecurityCenterClient(credentials=credentials)


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    tier = parameters.get("tier", "STANDARD").upper()
    if tier not in ("STANDARD", "PREMIUM"):
        raise ValueError(f"enable_security_command_center: tier must be STANDARD or PREMIUM, got {tier!r}")

    creds = connector.credentials if hasattr(connector, "credentials") else {}
    credentials = _get_credentials(creds)
    token = _token(credentials)

    project_id = _project_id(creds)
    sa_email = _sa_email(creds)

    # 1. Discover org
    org_id = parameters.get("org_id", "").strip() or _discover_org_id(token, project_id)
    if not org_id:
        raise RuntimeError(
            f"GCP project '{project_id}' has no parent organization. "
            "Complete the one-time setup: (1) create a Cloud Identity Free account at "
            "https://workspace.google.com/products/cloud-identity/ and link it to your domain, "
            f"(2) run: gcloud projects move {project_id} --organization=ORG_ID"
        )

    logger.info("enable_security_command_center: org_id=%s project=%s", org_id, project_id)

    # 2. Ensure SA has org-level SCC admin
    if sa_email and not _has_org_scc_role(token, org_id, sa_email):
        logger.info(
            "enable_security_command_center: SA lacks %s at org level — attempting self-grant",
            _SCC_ROLE,
        )
        granted = _grant_org_scc_role(token, org_id, sa_email)
        if not granted:
            raise RuntimeError(
                f"The service account '{sa_email}' lacks '{_SCC_ROLE}' at org {org_id} "
                "and could not self-grant it (insufficient org IAM permissions). "
                "Run once: gcloud organizations add-iam-policy-binding "
                f"{org_id} --member=serviceAccount:{sa_email} --role={_SCC_ROLE}"
            )
        # Re-mint token so the new binding is reflected
        token = _token(credentials)
        credentials = _get_credentials(creds)

    # 3. Enable SCC
    client = _scc_client(credentials)
    org_name = f"organizations/{org_id}"

    logger.info("enable_security_command_center: fetching org settings for %s", org_name)
    settings = client.get_organization_settings(name=f"{org_name}/organizationSettings")

    if settings.enable_asset_discovery:
        logger.info("enable_security_command_center: asset discovery already enabled for org %s", org_id)
        return {
            "org_id": org_id,
            "tier": tier,
            "asset_discovery_enabled": True,
            "status": "already_enabled",
        }

    settings.enable_asset_discovery = True
    client.update_organization_settings(
        organization_settings=settings,
        update_mask={"paths": ["enable_asset_discovery"]},
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
