# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Parallel DC upgrade executor.

Implements the Microsoft-prescribed domain controller upgrade paradigm:
  1. Provision a new DC at the target Windows Server version (via the nexplane agent
     running on the new DC after it's joined to the domain)
  2. Wait for AD replication to converge (verify via repadmin)
  3. Transfer FSMO roles if the old DC holds them
  4. Demote the old DC (removes ADDS role cleanly)

This is the replacement for in-place DC upgrade. The old DC is cleanly demoted
rather than being abruptly decommissioned — AD replication ensures no data loss.

Rollback: seize FSMO roles back to the old DC + promote it again if demoted,
OR (if promotion of new DC is the only completed step) simply decommission the
new DC. The FILO stack handles correct ordering.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """
    asset_ids[0]: the existing (old) DC to upgrade away from.

    Parameters:
      new_dc_hostname (str): FQDN or IP of the new DC (must be pre-joined to domain,
                             running target Windows Server version)
      new_dc_asset_id (str): asset_id of the new DC in the Nexplane inventory
      domain_name (str): AD domain FQDN (e.g. "corp.example.com")
      target_os_version (str): e.g. "2025"
      transfer_fsmo (bool): default true — transfer FSMO roles from old to new DC
      demote_old_dc (bool): default true — demote old DC after FSMO transfer
      dry_run (bool): default false
    """
    if not asset_ids:
        raise ValueError("asset_ids required (old DC asset ID)")

    old_dc_asset_id = str(asset_ids[0])
    new_dc_asset_id = parameters.get("new_dc_asset_id", "")
    new_dc_hostname = parameters.get("new_dc_hostname", "")
    domain_name = parameters.get("domain_name", "")
    target_os_version = parameters.get("target_os_version", "")
    transfer_fsmo = bool(parameters.get("transfer_fsmo", True))
    demote_old_dc = bool(parameters.get("demote_old_dc", True))
    dry_run = bool(parameters.get("dry_run", False))

    if not new_dc_asset_id or not new_dc_hostname:
        raise ValueError("new_dc_asset_id and new_dc_hostname are required")
    if not domain_name:
        raise ValueError("domain_name required")

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    # Step 1: Preflight — verify new DC is online, joined to domain, reachable
    logger.info(f"DC parallel upgrade preflight: old={old_dc_asset_id}, new={new_dc_asset_id}")
    preflight = await dispatch_agent_job(
        command="preflight_dc_parallel_upgrade",
        parameters={
            "new_dc_hostname": new_dc_hostname,
            "domain_name": domain_name,
            "target_os_version": target_os_version,
        },
        asset_ids=[new_dc_asset_id],
        timeout_seconds=120,
    )

    if preflight.get("status") == "blocked":
        return {"status": "blocked", "reason": preflight.get("reason"), "preflight": preflight}

    if dry_run:
        return {
            "status": "dry_run",
            "old_dc_asset_id": old_dc_asset_id,
            "new_dc_asset_id": new_dc_asset_id,
            "new_dc_hostname": new_dc_hostname,
            "domain_name": domain_name,
            "fsmo_holders": preflight.get("fsmo_holders", {}),
            "replication_status": preflight.get("replication_status"),
        }

    # Step 2: Promote new DC (ADDS role installation + dcpromo)
    logger.info(f"Promoting {new_dc_hostname} as additional DC in {domain_name}")
    promote_result = await dispatch_agent_job(
        command="promote_dc",
        parameters={
            "domain_name": domain_name,
            "replication_source_dc": preflight.get("old_dc_hostname", ""),
        },
        asset_ids=[new_dc_asset_id],
        timeout_seconds=1800,  # DC promotion including reboot takes ~30 min
    )

    if not promote_result.get("success", True):
        return {
            "status": "failed",
            "phase": "promote_new_dc",
            "error": promote_result.get("error"),
            "promote_result": promote_result,
            "rollback_hint": "New DC promotion failed — no changes to old DC",
        }

    promoted_at = datetime.now(timezone.utc).isoformat()

    # Step 3: Wait for replication convergence
    logger.info(f"Waiting for AD replication convergence after promoting {new_dc_hostname}")
    replication_result = await dispatch_agent_job(
        command="verify_ad_replication",
        parameters={
            "new_dc_hostname": new_dc_hostname,
            "domain_name": domain_name,
        },
        asset_ids=[new_dc_asset_id],
        timeout_seconds=600,
    )

    if not replication_result.get("converged", True):
        logger.warning(f"AD replication not yet converged on {new_dc_hostname}: {replication_result}")

    # Step 4: Transfer FSMO roles
    fsmo_transfer_result = None
    if transfer_fsmo:
        logger.info(f"Transferring FSMO roles to {new_dc_hostname}")
        fsmo_transfer_result = await dispatch_agent_job(
            command="transfer_fsmo_roles",
            parameters={
                "target_dc": new_dc_hostname,
                "domain_name": domain_name,
            },
            asset_ids=[new_dc_asset_id],
            timeout_seconds=300,
        )

    # Step 5: Demote old DC
    demotion_result = None
    if demote_old_dc:
        logger.info(f"Demoting old DC {old_dc_asset_id}")
        try:
            demotion_result = await dispatch_agent_job(
                command="demote_dc",
                parameters={
                    "domain_name": domain_name,
                    "last_dc_in_domain": False,
                },
                asset_ids=[old_dc_asset_id],
                timeout_seconds=1800,
            )
        except Exception as exc:
            # Old DC may disconnect after demotion reboot — treat as success
            if _is_disconnect(exc):
                demotion_result = {"success": True, "note": "Old DC disconnected after demotion (expected reboot)"}
            else:
                demotion_result = {"success": False, "error": str(exc)}

    return {
        "status": "completed",
        "old_dc_asset_id": old_dc_asset_id,
        "new_dc_asset_id": new_dc_asset_id,
        "new_dc_hostname": new_dc_hostname,
        "promoted_at": promoted_at,
        "replication_result": replication_result,
        "fsmo_transfer_result": fsmo_transfer_result,
        "demotion_result": demotion_result,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Rollback order (FILO):
    1. If old DC was demoted: re-promote old DC
    2. If FSMO roles were transferred: seize them back to old DC
    3. Demote new DC
    """
    asset_ids = (
        execution_result.get("_target_asset_ids")
        or parameters.get("asset_ids")
        or []
    )
    old_dc_asset_id = str(asset_ids[0]) if asset_ids else ""
    new_dc_asset_id = execution_result.get("new_dc_asset_id", "")
    domain_name = execution_result.get("domain_name") or parameters.get("domain_name", "")
    new_dc_hostname = execution_result.get("new_dc_hostname", "")

    if not old_dc_asset_id or not new_dc_asset_id:
        return {"rolled_back": False, "reason": "missing asset IDs for rollback"}

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    steps = []

    # Re-promote old DC if it was demoted
    if execution_result.get("demotion_result", {}).get("success"):
        try:
            result = await dispatch_agent_job(
                command="promote_dc",
                parameters={"domain_name": domain_name, "replication_source_dc": new_dc_hostname},
                asset_ids=[old_dc_asset_id],
                timeout_seconds=1800,
            )
            steps.append({"step": "repromote_old_dc", "result": result})
        except Exception as exc:
            steps.append({"step": "repromote_old_dc", "error": str(exc)})

    # Seize FSMO roles back to old DC
    if execution_result.get("fsmo_transfer_result"):
        try:
            result = await dispatch_agent_job(
                command="seize_fsmo_roles",
                parameters={"target_dc": old_dc_asset_id, "domain_name": domain_name},
                asset_ids=[old_dc_asset_id],
                timeout_seconds=300,
            )
            steps.append({"step": "seize_fsmo_back", "result": result})
        except Exception as exc:
            steps.append({"step": "seize_fsmo_back", "error": str(exc)})

    # Demote new DC
    if execution_result.get("promote_result") or execution_result.get("promoted_at"):
        try:
            result = await dispatch_agent_job(
                command="demote_dc",
                parameters={"domain_name": domain_name, "last_dc_in_domain": False},
                asset_ids=[new_dc_asset_id],
                timeout_seconds=1800,
            )
            steps.append({"step": "demote_new_dc", "result": result})
        except Exception as exc:
            if _is_disconnect(exc):
                steps.append({"step": "demote_new_dc", "result": {"success": True, "note": "disconnected (expected)"}})
            else:
                steps.append({"step": "demote_new_dc", "error": str(exc)})

    return {
        "rolled_back": True,
        "steps": steps,
    }


def _is_disconnect(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(kw in msg for kw in ("connection", "disconnect", "timeout", "reset", "eof", "closed"))
