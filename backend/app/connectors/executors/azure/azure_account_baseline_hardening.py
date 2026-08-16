# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Azure account baseline hardening executor.

Assigns the CIS Azure Foundations Benchmark policy initiative
(/providers/Microsoft.Authorization/policySetDefinitions/1a5aa27d-2fae-49da-9c7e-70e94cca8eda)
to the subscription scope.

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


async def _run(fn):
    return await asyncio.get_running_loop().run_in_executor(None, fn)


def _get_policy_client(creds: dict):
    from azure.identity import ClientSecretCredential
    from azure.mgmt.resource.policy import PolicyClient
    credential = ClientSecretCredential(
        creds["tenant_id"], creds["client_id"], creds["client_secret"]
    )
    return PolicyClient(credential, creds["subscription_id"])


async def _preflight(creds: dict) -> dict:
    def _do():
        _get_policy_client(creds)  # validates credentials
        return creds["subscription_id"]

    sub_id = await _run(_do)
    return {"phase": "preflight", "status": "ok", "subscription_id": sub_id}


async def _snapshot(creds: dict, sub_id: str) -> dict:
    def _do():
        client = _get_policy_client(creds)
        scope = f"/subscriptions/{sub_id}"
        assignments = list(client.policy_assignments.list_for_scope(scope=scope))
        existing = [a.name for a in assignments if _ASSIGNMENT_NAME in (a.name or "")]
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
        from azure.mgmt.resource.policy.models import PolicyAssignment
        client = _get_policy_client(creds)
        scope = f"/subscriptions/{sub_id}"
        client.policy_assignments.create(
            scope=scope,
            policy_assignment_name=_ASSIGNMENT_NAME,
            parameters=PolicyAssignment(
                display_name="Nexplane CIS Azure Foundations Baseline",
                policy_definition_id=_CIS_INITIATIVE_ID,
                enforcement_mode="Default",
            ),
        )

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
        client = _get_policy_client(creds)
        scope = f"/subscriptions/{sub_id}"
        assignments = list(client.policy_assignments.list_for_scope(scope=scope))
        names = [a.name for a in assignments]
        failures = [name for name in applied if name not in names]
        return failures

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
            from azure.core.exceptions import ResourceNotFoundError
            client = _get_policy_client(creds)
            scope = f"/subscriptions/{sub_id}"
            try:
                client.policy_assignments.delete(scope=scope, policy_assignment_name=n)
            except ResourceNotFoundError:
                pass

        await _run(_delete)
        deleted.append(name)
        logger.info("azure_account_baseline_hardening rollback: deleted assignment %s", name)

    return {
        "rolled_back": True,
        "deleted_assignments": deleted,
        "rolled_back_at": datetime.now(timezone.utc).isoformat(),
    }
