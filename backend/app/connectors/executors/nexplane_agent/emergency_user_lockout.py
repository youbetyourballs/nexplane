# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"


def _okta_client_from_params(parameters: dict):
    """Extract Okta credentials from parameters for emergency lockout."""
    org_url = parameters.get("okta_org_url") or parameters.get("org_url")
    api_token = parameters.get("okta_api_token") or parameters.get("api_token")
    if not org_url or not api_token:
        return None, None
    base = org_url.rstrip("/") + "/api/v1"
    headers = {
        "Authorization": f"SSWS {api_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    return base, headers


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    user = parameters.get("user_identifier", "")
    systems = parameters.get("systems", ["aws_iam", "azure_ad", "linux_local"])
    results = {}
    errors = []

    for system in systems:
        try:
            if system == "aws_iam":
                from app.connectors.executors.aws.lock_iam_user import execute as iam_lock
                await iam_lock({"user_name": user}, asset_ids, connector)
                results["aws_iam"] = "locked"
            elif system == "azure_ad":
                from app.connectors.executors.azure_ad.disable_user import execute as aad_disable
                await aad_disable({"user_identifier": user}, asset_ids, connector)
                results["azure_ad"] = "locked"
            elif system == "linux_local":
                from app.connectors.executors.nexplane_agent import _dispatch
                await _dispatch.dispatch_agent_job(
                    command="lock_local_user",
                    parameters={"username": user, "terminate_sessions": True},
                    asset_ids=list(asset_ids),
                    timeout_seconds=30,
                )
                results["linux_local"] = "locked"
            elif system == "okta":
                import httpx
                base, headers = _okta_client_from_params(parameters)
                if base:
                    async with httpx.AsyncClient(timeout=30) as c:
                        resp = await c.post(f"{base}/users/{user}/lifecycle/suspend", headers=headers)
                        resp.raise_for_status()
                    results["okta"] = "locked"
                else:
                    results["okta"] = "skipped_no_credentials"
            elif system == "ldap":
                from app.connectors.executors.ldap.disable_user import execute as ldap_disable

                class _InlineLDAP:
                    credentials = {
                        "host": parameters.get("ldap_host"),
                        "port": parameters.get("ldap_port", 389),
                        "bind_dn": parameters.get("ldap_bind_dn"),
                        "bind_password": parameters.get("ldap_bind_password"),
                        "base_dn": parameters.get("ldap_base_dn", "dc=example,dc=com"),
                    }

                inline = _InlineLDAP() if parameters.get("ldap_host") else connector
                r = await ldap_disable({"username": user}, asset_ids, inline)
                results["ldap"] = "locked" if r.get("success") or r.get("status") != "skipped" else "skipped_no_credentials"
            elif system == "keycloak":
                from app.connectors.executors.keycloak.disable_user import execute as kc_disable
                class _InlineKeycloak:
                    credentials = {
                        "url": parameters.get("keycloak_url"),
                        "realm": parameters.get("keycloak_realm", "master"),
                        "client_id": parameters.get("keycloak_client_id", "admin-cli"),
                        "username": parameters.get("keycloak_admin"),
                        "password": parameters.get("keycloak_password"),
                    }
                inline = _InlineKeycloak() if parameters.get("keycloak_url") else connector
                r = await kc_disable({"username": user}, asset_ids, inline)
                results["keycloak"] = "locked" if r.get("success") else "skipped_no_credentials"
        except Exception as e:
            results[system] = "failed"
            errors.append({"system": system, "error": str(e)})

    return {
        "action": "emergency_user_lockout",
        "user_identifier": user,
        "lockout_status": results,
        "errors": errors,
        "locked_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    user = parameters.get("user_identifier", "")
    rollback_results = {}

    # Always attempt unconditionally — delete_user_policy is idempotent,
    # and execution_result structure varies depending on how the workflow wraps it.
    try:
        from app.connectors.executors.aws.lock_iam_user import rollback as iam_unlock
        r = await iam_unlock({"user_identifier": user}, execution_result, connector)
        rollback_results["aws_iam"] = "unlocked" if r.get("rolled_back") else "failed"
    except Exception as e:
        rollback_results["aws_iam"] = f"error: {e}"

    try:
        from app.connectors.executors.azure_ad.disable_user import rollback as aad_restore
        r = await aad_restore({"user_identifier": user}, execution_result, connector)
        rollback_results["azure_ad"] = "unlocked" if r.get("rolled_back") else "skipped"
    except Exception as e:
        rollback_results["azure_ad"] = f"error: {e}"

    if execution_result.get("lockout_status", {}).get("ldap") == "locked":
        try:
            from app.connectors.executors.ldap.disable_user import rollback as ldap_restore
            r = await ldap_restore({"username": user}, execution_result, connector)
            rollback_results["ldap"] = "unlocked" if r.get("rolled_back") else "failed"
        except Exception as e:
            rollback_results["ldap"] = f"error: {e}"

    return {"rolled_back": True, "systems": rollback_results}
