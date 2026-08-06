# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
FreeIPA / RHIDM upgrade executor.
Flow: preflight -> backup -> upgrade replicas -> upgrade master -> verify -> (rollback via ipa-backup restore).
Topology rule: replicas must be upgraded before master.
ROLLBACK_CAPABILITY = "partial" — ipa-backup restore wipes+reinitializes LDAP+Kerberos DBs;
changes after backup time are lost. Replicas self-heal from master post-restore.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "partial"


def _resolve_params(parameters: dict) -> dict:
    p = parameters.get("desired_outcome") or parameters
    return {
        "source_version":     p.get("source_version"),
        "target_version":     p["target_version"],
        "master_host":        p["master_host"],
        "replica_hosts":      p.get("replica_hosts") or [],
        "ipa_admin_password": p.get("ipa_admin_password"),
        "dry_run":            bool(p.get("dry_run", False)),
    }


def _get_dispatch():
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    return dispatch_agent_job


async def dispatch_agent_job(command, parameters, asset_ids, timeout_seconds=300):
    fn = _get_dispatch()
    return await fn(
        command=command,
        parameters=parameters,
        asset_ids=asset_ids,
        timeout_seconds=timeout_seconds,
    )


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    p = _resolve_params(parameters)

    # --- Phase 1: Preflight ---
    preflight_result = await dispatch_agent_job(
        command="ipa_status_check",
        parameters=p,
        asset_ids=[asset_id],
        timeout_seconds=120,
    )
    if preflight_result.get("status") == "preflight_blocked":
        return preflight_result

    if p["dry_run"]:
        return {**preflight_result, "dry_run": True}

    # --- Phase 2: Backup ---
    backup_result = await dispatch_agent_job(
        command="ipa_backup",
        parameters=p,
        asset_ids=[asset_id],
        timeout_seconds=1800,
    )
    backup_path = backup_result.get("backup_path")
    logger.info(f"IPA backup at: {backup_path}")

    # --- Phase 3: Upgrade replicas (before master — IPA topology rule) ---
    replicas_upgraded = []
    for replica_host in p["replica_hosts"]:
        logger.info(f"Upgrading IPA replica: {replica_host}")
        await dispatch_agent_job(
            command="ipa_server_upgrade",
            parameters={**p, "target_host": replica_host},
            asset_ids=[asset_id],
            timeout_seconds=1800,
        )
        replicas_upgraded.append(replica_host)
        logger.info(f"Replica {replica_host} upgraded")

    # --- Phase 4: Upgrade master ---
    logger.info(f"Upgrading IPA master: {p['master_host']}")
    await dispatch_agent_job(
        command="ipa_server_upgrade",
        parameters={**p, "target_host": p["master_host"]},
        asset_ids=[asset_id],
        timeout_seconds=1800,
    )

    # --- Phase 5: Verify ---
    verify_result = await dispatch_agent_job(
        command="ipa_verify",
        parameters=p,
        asset_ids=[asset_id],
        timeout_seconds=120,
    )
    ipactl_ok  = verify_result.get("ipactl_ok", False)
    kinit_ok   = verify_result.get("kinit_ok", False)
    verify_ok  = ipactl_ok and kinit_ok

    return {
        "status":            "completed" if verify_ok else "verify_failed",
        "source_version":    p["source_version"],
        "target_version":    p["target_version"],
        "master_upgraded":   p["master_host"],
        "replicas_upgraded": replicas_upgraded,
        "backup_path":       backup_path,
        "verify_result":     verify_result,
        "asset_id":          asset_id,
        "upgraded_at":       datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """
    Restore from ipa-backup on master. Data loss: all changes after backup_path timestamp.
    Replicas self-heal from master after restore.
    """
    backup_path = execution_result.get("backup_path")
    if not backup_path:
        return {"rolled_back": False, "reason": "no_backup_path_in_execution_result"}

    asset_id = execution_result.get("asset_id") or str(
        (parameters.get("asset_ids") or [None])[0]
    )
    p = _resolve_params(parameters)

    try:
        result = await dispatch_agent_job(
            command="ipa_backup_restore",
            parameters={**p, "backup_path": backup_path},
            asset_ids=[asset_id],
            timeout_seconds=1800,
        )
        return {
            "rolled_back":   True,
            "strategy":      "ipa_backup_restore",
            "backup_path":   backup_path,
            "data_loss_warning": (
                "All IPA changes after backup time are lost. "
                "Replicas will self-heal from master after restore."
            ),
            "agent_result":  result,
        }
    except Exception as exc:
        logger.error(f"FreeIPA rollback failed: {exc}")
        return {"rolled_back": False, "reason": str(exc)}
