# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Azure account baseline hardening executor.

Assigns the CIS Azure Foundations Benchmark policy initiative
(/providers/Microsoft.Authorization/policySetDefinitions/1a5aa27d-2fae-49da-9c7e-70e94cca8eda)
to the subscription scope using the Azure ARM REST API directly.

Rollback: delete the policy assignment.
"""

import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"

_CIS_INITIATIVE_ID = (
    "/providers/Microsoft.Authorization/policySetDefinitions/"
    "1a5aa27d-2fae-49da-9c7e-70e94cca8eda"
)
_ASSIGNMENT_NAME = "nexplane-cis-baseline"
_API_VERSION = "2022-06-01"
_ARM_BASE = "https://management.azure.com"


async def _run(fn):
    return await asyncio.get_running_loop().run_in_executor(None, fn)


def _get_token(creds: dict) -> str:
    from azure.identity import ClientSecretCredential
    credential = ClientSecretCredential(
        creds["tenant_id"], creds["client_id"], creds["client_secret"]
    )
    token = credential.get_token("https://management.azure.com/.default")
    return token.token


def _arm_get(token: str, url: str) -> dict:
    import requests
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _arm_put(token: str, url: str, body: dict) -> dict:
    import requests
    resp = requests.put(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _arm_delete(token: str, url: str) -> None:
    import requests
    resp = requests.delete(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    if resp.status_code not in (200, 204, 404):
        resp.raise_for_status()


async def _preflight(creds: dict) -> dict:
    def _do():
        _get_token(creds)  # validates credentials
        return creds["subscription_id"]

    sub_id = await _run(_do)
    return {"phase": "preflight", "status": "ok", "subscription_id": sub_id}


async def _snapshot(creds: dict, sub_id: str) -> dict:
    def _do():
        token = _get_token(creds)
        scope = f"/subscriptions/{sub_id}"
        url = (
            f"{_ARM_BASE}{scope}/providers/Microsoft.Authorization/policyAssignments"
            f"?api-version={_API_VERSION}"
        )
        data = _arm_get(token, url)
        existing = [a["name"] for a in data.get("value", []) if a["name"] == _ASSIGNMENT_NAME]
        return existing

    existing = await _run(_do)
    return {"phase": "snapshot", "status": "ok", "pre": {"existing_assignments": existing}}


async def _enable(creds: dict, sub_id: str, pre: dict) -> dict:
    existing = pre.get("existing_assignments", [])
    rollback_data = {"assignment_names": []}

    if _ASSIGNMENT_NAME in existing:
        return {
            "phase": "enable", "status": "ok",
            "applied": [], "skipped": [_ASSIGNMENT_NAME],
            "rollback_data": rollback_data,
        }

    def _assign():
        token = _get_token(creds)
        scope = f"/subscriptions/{sub_id}"
        url = (
            f"{_ARM_BASE}{scope}/providers/Microsoft.Authorization/policyAssignments"
            f"/{_ASSIGNMENT_NAME}?api-version={_API_VERSION}"
        )
        body = {
            "properties": {
                "displayName": "Nexplane CIS Azure Foundations Baseline",
                "policyDefinitionId": _CIS_INITIATIVE_ID,
                "enforcementMode": "Default",
            }
        }
        _arm_put(token, url, body)

    await _run(_assign)
    rollback_data["assignment_names"].append(_ASSIGNMENT_NAME)
    logger.info("azure_account_baseline_hardening: assigned CIS initiative to sub %s", sub_id)

    return {
        "phase": "enable", "status": "ok",
        "applied": [_ASSIGNMENT_NAME], "skipped": [],
        "rollback_data": rollback_data,
    }


async def _verify(creds: dict, sub_id: str, applied: list) -> dict:
    if not applied:
        return {"phase": "verify", "status": "ok", "failures": []}

    def _do():
        token = _get_token(creds)
        scope = f"/subscriptions/{sub_id}"
        url = (
            f"{_ARM_BASE}{scope}/providers/Microsoft.Authorization/policyAssignments"
            f"/{_ASSIGNMENT_NAME}?api-version={_API_VERSION}"
        )
        import requests
        resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
        if resp.status_code == 404:
            return applied  # all applied names are failures
        resp.raise_for_status()
        return []

    failures = await _run(_do)
    return {"phase": "verify", "status": "failed" if failures else "ok", "failures": failures}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = connector.credentials

    preflight = await _preflight(creds)
    if preflight["status"] != "ok":
        return preflight
    sub_id = preflight["subscription_id"]

    snapshot = await _snapshot(creds, sub_id)
    enable = await _enable(creds, sub_id, snapshot["pre"])
    verify = await _verify(creds, sub_id, enable["applied"])

    return {
        "phase": "report",
        "status": verify["status"],
        "subscription_id": sub_id,
        "applied": enable["applied"],
        "skipped": enable["skipped"],
        "verify_failures": verify["failures"],
        "rollback_data": enable["rollback_data"],
        "hardened_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = connector.credentials
    sub_id = execution_result.get("subscription_id", creds["subscription_id"])
    rollback_data = execution_result.get("rollback_data", {})
    assignment_names = rollback_data.get("assignment_names", [])

    deleted = []
    for name in reversed(assignment_names):
        def _delete(n=name):
            token = _get_token(creds)
            scope = f"/subscriptions/{sub_id}"
            url = (
                f"{_ARM_BASE}{scope}/providers/Microsoft.Authorization/policyAssignments"
                f"/{n}?api-version={_API_VERSION}"
            )
            _arm_delete(token, url)

        await _run(_delete)
        deleted.append(name)
        logger.info("azure_account_baseline_hardening rollback: deleted assignment %s", name)

    return {
        "rolled_back": True,
        "deleted_assignments": deleted,
        "rolled_back_at": datetime.now(timezone.utc).isoformat(),
    }
