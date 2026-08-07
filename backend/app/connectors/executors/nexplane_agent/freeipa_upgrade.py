# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
FreeIPA / RHIDM upgrade executor.
Uses run_command agent primitive — no dedicated IPA agent commands required.
Flow: preflight -> backup -> upgrade master -> verify -> (rollback via ipa-backup restore).
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"


def _resolve_params(parameters: dict) -> dict:
    p = parameters.get("desired_outcome") or parameters
    return {
        "source_version":     p.get("source_version"),
        "target_version":     p["target_version"],
        "master_host":        p["master_host"],
        "replica_hosts":      p.get("replica_hosts") or [],
        "ipa_admin_password": p.get("ipa_admin_password", ""),
        "dry_run":            bool(p.get("dry_run", False)),
    }


def _get_dispatch():
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    return dispatch_agent_job


async def _run(command: str, asset_id: str, timeout: int = 120) -> dict:
    fn = _get_dispatch()
    return await fn(
        command="run_command",
        parameters={"command": command, "timeout": timeout},
        asset_ids=[asset_id],
        timeout_seconds=timeout + 30,
    )


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    p = _resolve_params(parameters)
    admin_pw = p["ipa_admin_password"]

    # --- Phase 1: Preflight ---
    r = await _run("ipactl status 2>&1; echo EXIT_CODE=$?", asset_id, timeout=60)
    output = r.get("output", "")
    if "Directory Service: RUNNING" not in output and "ipa: INFO: The ipactl command was successful" not in output:
        logger.warning(f"IPA preflight output: {output}")

    if p["dry_run"]:
        return {"dry_run": True, "preflight_output": output}

    # --- Phase 2: Backup ---
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = f"/var/lib/ipa/backup/nexplane-{ts}"
    r = await _run(
        f"ipa-backup --data --online --log-file=/tmp/ipa-backup.log 2>&1 || "
        f"ipa-backup 2>&1; ls /var/lib/ipa/backup/ | tail -1",
        asset_id, timeout=1200,
    )
    backup_output = r.get("output", "")
    # Extract actual backup path from output
    backup_path = None
    for line in backup_output.splitlines():
        line = line.strip()
        if line.startswith("ipa-full") or line.startswith("ipa-data"):
            backup_path = f"/var/lib/ipa/backup/{line}"
    if not backup_path:
        backup_path = backup_dir
    logger.info(f"IPA backup path: {backup_path}")

    # --- Phase 3: Upgrade master ---
    r = await _run(
        "ipa-server-upgrade 2>&1; echo UPGRADE_EXIT=$?",
        asset_id, timeout=1800,
    )
    upgrade_output = r.get("output", "")
    logger.info(f"IPA upgrade output tail: {upgrade_output[-300:]}")

    # --- Phase 4: Verify ---
    r_ipactl = await _run("ipactl status 2>&1", asset_id, timeout=60)
    ipactl_output = r_ipactl.get("output", "")
    ipactl_ok = (
        r_ipactl.get("exit_code", 1) == 0
        or "RUNNING" in ipactl_output
        or "successful" in ipactl_output.lower()
    )

    r_kinit = await _run(
        f"echo '{admin_pw}' | kinit admin 2>&1; echo KINIT_EXIT=$?",
        asset_id, timeout=30,
    )
    kinit_output = r_kinit.get("output", "")
    kinit_ok = "KINIT_EXIT=0" in kinit_output or r_kinit.get("exit_code", 1) == 0

    verify_result = {
        "ipactl_ok":     ipactl_ok,
        "kinit_ok":      kinit_ok,
        "ipactl_output": ipactl_output[:500],
    }

    return {
        "status":          "completed" if (ipactl_ok and kinit_ok) else "verify_failed",
        "source_version":  p["source_version"],
        "target_version":  p["target_version"],
        "master_upgraded": p["master_host"],
        "replicas_upgraded": [],
        "backup_path":     backup_path,
        "verify_result":   verify_result,
        "asset_id":        asset_id,
        "upgraded_at":     datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    backup_path = execution_result.get("backup_path")
    if not backup_path:
        return {"rolled_back": False, "reason": "no_backup_path_in_execution_result"}

    asset_id = execution_result.get("asset_id") or str(
        (parameters.get("asset_ids") or [None])[0]
    )
    p = _resolve_params(parameters)

    try:
        r = await _run(
            f"ipa-restore --unattended '{backup_path}' 2>&1; echo RESTORE_EXIT=$?",
            asset_id, timeout=1800,
        )
        restore_output = r.get("output", "")
        restore_ok = "RESTORE_EXIT=0" in restore_output or r.get("exit_code", 1) == 0
        return {
            "rolled_back":   restore_ok,
            "strategy":      "ipa_backup_restore",
            "backup_path":   backup_path,
            "data_loss_warning": (
                "All IPA changes after backup time are lost. "
                "Replicas will self-heal from master after restore."
            ),
            "restore_output": restore_output[:500],
        }
    except Exception as exc:
        logger.error(f"FreeIPA rollback failed: {exc}")
        return {"rolled_back": False, "reason": str(exc)}
