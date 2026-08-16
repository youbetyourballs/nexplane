# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""GCP account baseline hardening executor.

Enforces four org-level policies on the GCP project:
  - constraints/compute.requireOsLogin
  - constraints/compute.disableSerialPortAccess
  - constraints/storage.publicAccessPrevention
  - constraints/iam.disableServiceAccountKeyCreation

Rollback: restore prior policy state for each constraint that was newly set.
"""

import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"

HARDENING_CONSTRAINTS = [
    "compute.requireOsLogin",
    "compute.disableSerialPortAccess",
    "storage.publicAccessPrevention",
    "iam.disableServiceAccountKeyCreation",
]


async def _run(fn):
    return await asyncio.get_running_loop().run_in_executor(None, fn)


async def _preflight(creds: dict) -> dict:
    def _do():
        from app.connectors.executors.gcp._client import get_credentials, get_project_id
        get_credentials(creds)  # validates key
        return get_project_id(creds)

    project_id = await _run(_do)
    return {"phase": "preflight", "status": "ok", "project_id": project_id}


async def _snapshot(creds: dict, project_id: str) -> dict:
    def _do():
        from google.cloud import orgpolicy_v2
        from google.api_core.exceptions import NotFound
        from app.connectors.executors.gcp._client import get_credentials
        credentials = get_credentials(creds)
        client = orgpolicy_v2.OrgPoliciesClient(credentials=credentials)
        results = {}
        for constraint in HARDENING_CONSTRAINTS:
            name = f"projects/{project_id}/policies/{constraint}"
            try:
                policy = client.get_policy(name=name)
                results[constraint] = {
                    "exists": True,
                    "enforce": any(
                        getattr(r, "enforce", False)
                        for r in (policy.spec.rules if policy.spec else [])
                    ),
                }
            except NotFound:
                results[constraint] = {"exists": False, "enforce": False}
        return results

    pre_policies = await _run(_do)
    return {"phase": "snapshot", "status": "ok", "pre": pre_policies}


async def _enable(creds: dict, project_id: str, pre: dict) -> dict:
    applied = []
    skipped = []
    rollback_data = {"newly_applied": [], "pre_policies": pre}

    for constraint in HARDENING_CONSTRAINTS:
        prior = pre.get(constraint, {})
        if prior.get("enforce"):
            skipped.append(constraint)
            continue

        def _apply(c=constraint):
            from google.cloud import orgpolicy_v2
            from app.connectors.executors.gcp._client import get_credentials
            credentials = get_credentials(creds)
            client = orgpolicy_v2.OrgPoliciesClient(credentials=credentials)
            policy = orgpolicy_v2.Policy(
                name=f"projects/{project_id}/policies/{c}",
                spec=orgpolicy_v2.PolicySpec(
                    rules=[orgpolicy_v2.PolicySpec.PolicyRule(enforce=True)]
                ),
            )
            client.update_policy(policy=policy)

        try:
            await _run(_apply)
            applied.append(constraint)
            rollback_data["newly_applied"].append(constraint)
            logger.info("gcp_account_baseline_hardening: applied %s", constraint)
        except Exception as e:
            logger.error("gcp_account_baseline_hardening: failed to apply %s: %s", constraint, e)
            skipped.append(f"{constraint} (error: {e})")

    return {"phase": "enable", "status": "ok", "applied": applied, "skipped": skipped,
            "rollback_data": rollback_data}


async def _verify(creds: dict, project_id: str, applied: list) -> dict:
    def _do():
        from google.cloud import orgpolicy_v2
        from app.connectors.executors.gcp._client import get_credentials
        credentials = get_credentials(creds)
        client = orgpolicy_v2.OrgPoliciesClient(credentials=credentials)
        failures = []
        for constraint in applied:
            name = f"projects/{project_id}/policies/{constraint}"
            try:
                policy = client.get_policy(name=name)
                enforced = any(
                    getattr(r, "enforce", False)
                    for r in (policy.spec.rules if policy.spec else [])
                )
                if not enforced:
                    failures.append(f"{constraint}: not enforced after apply")
            except Exception as e:
                failures.append(f"{constraint}: verify error {e}")
        return failures

    failures = await _run(_do)
    status = "failed" if failures else "ok"
    return {"phase": "verify", "status": status, "failures": failures}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = connector.credentials

    preflight = await _preflight(creds)
    if preflight["status"] != "ok":
        return preflight
    project_id = preflight["project_id"]

    snapshot = await _snapshot(creds, project_id)
    pre = snapshot["pre"]

    enable = await _enable(creds, project_id, pre)
    verify = await _verify(creds, project_id, enable["applied"])

    return {
        "phase": "report",
        "status": verify["status"],
        "project_id": project_id,
        "applied": enable["applied"],
        "skipped": enable["skipped"],
        "verify_failures": verify.get("failures", []),
        "rollback_data": enable["rollback_data"],
        "hardened_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = connector.credentials
    rollback_data = execution_result.get("rollback_data", {})
    newly_applied = rollback_data.get("newly_applied", [])
    pre_policies = rollback_data.get("pre_policies", {})
    project_id = execution_result.get("project_id", "")

    restored = []
    for constraint in reversed(newly_applied):
        prior = pre_policies.get(constraint, {})

        def _restore(c=constraint, p=prior):
            from google.cloud import orgpolicy_v2
            from google.api_core.exceptions import NotFound
            from app.connectors.executors.gcp._client import get_credentials
            credentials = get_credentials(creds)
            client = orgpolicy_v2.OrgPoliciesClient(credentials=credentials)
            name = f"projects/{project_id}/policies/{c}"
            if not p.get("exists"):
                try:
                    client.delete_policy(name=name)
                except NotFound:
                    pass
            else:
                policy = orgpolicy_v2.Policy(
                    name=name,
                    spec=orgpolicy_v2.PolicySpec(
                        rules=[orgpolicy_v2.PolicySpec.PolicyRule(enforce=p.get("enforce", False))]
                    ),
                )
                client.update_policy(policy=policy)

        try:
            await _run(_restore)
            restored.append(constraint)
            logger.info("gcp_account_baseline_hardening rollback: restored %s", constraint)
        except Exception as e:
            logger.error("gcp_account_baseline_hardening rollback: error restoring %s: %s", constraint, e)

    return {
        "rolled_back": True,
        "restored": restored,
        "rolled_back_at": datetime.now(timezone.utc).isoformat(),
    }
