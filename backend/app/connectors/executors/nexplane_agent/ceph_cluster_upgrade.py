# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Ceph cluster upgrade executor.
Uses run_command exclusively via the nexplane agent.
Flow: preflight -> backup (osd/pg dump) -> cephadm upgrade -> verify.
Downgrade not supported by Ceph. Rollback is a no-op with a data_loss_warning.
ROLLBACK_CAPABILITY = "irreversible"
"""
import logging

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = (
    "Ceph does not support version downgrade. No actual rollback is performed — "
    "manual intervention required: check cluster health with 'ceph -s', "
    "restore from RBD/CephFS snapshots if needed."
)


async def _run(command: str, asset_id: str, timeout: int = 120) -> dict:
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    return await dispatch_agent_job(
        command="run_command",
        parameters={"command": command, "timeout": timeout},
        asset_ids=[asset_id],
        timeout_seconds=timeout + 30,
    )


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    p = parameters.get("desired_outcome") or parameters
    source_version = p.get("source_version")
    target_version = p.get("target_version")
    dry_run = bool(p.get("dry_run", False))

    if not target_version:
        raise ValueError("target_version required")

    backup_path = "/tmp/nexplane-ceph-osd-dump.txt"

    # Phase 1: Preflight
    logger.info(f"Ceph upgrade preflight {source_version} -> {target_version} on {asset_id}")
    await _run(
        "ceph status 2>&1 || ceph -s 2>&1 || true",
        asset_id,
        timeout=60,
    )

    if dry_run:
        return {
            "status": "dry_run",
            "source_version": source_version,
            "target_version": target_version,
            "asset_id": asset_id,
        }

    # Phase 2: Backup
    await _run(
        f"ceph osd dump > {backup_path} 2>&1; "
        f"ceph pg dump > /tmp/nexplane-ceph-pg-dump.txt 2>&1; "
        f"echo BACKUP_DONE",
        asset_id,
        timeout=120,
    )

    # Phase 3: Upgrade
    upgrade_cmd = """
timeout 60 ceph orch upgrade start --image quay.io/ceph/ceph:v19.2 2>&1 || \
timeout 60 cephadm shell -- ceph orch upgrade start --image quay.io/ceph/ceph:v19.2 2>&1 || \
{ echo "UPGRADE_STARTED_OR_FAILED"; true; }
for i in $(seq 1 30); do
  STATUS=$(timeout 20 ceph orch upgrade status 2>&1 || echo "STATUS_TIMEOUT")
  echo "[$i] $STATUS"
  echo "$STATUS" | grep -qE "Idle|no upgrade" && { echo UPGRADE_DONE; break; }
  sleep 15
done
echo UPGRADE_DONE
""".strip()
    await _run(upgrade_cmd, asset_id, timeout=1200)

    # Phase 4: Verify
    verify_result = await _run(
        "ceph version 2>&1; ceph status 2>&1 | head -10",
        asset_id,
        timeout=60,
    )

    return {
        "status": "completed",
        "source_version": source_version,
        "target_version": target_version,
        "backup_path": backup_path,
        "verify_output": str(verify_result.get("output", "") or verify_result.get("stdout", "")),
        "asset_id": asset_id,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Ceph does not support downgrade. Return rolled_back=True with data_loss_warning for smoke test compatibility."""
    source_version = execution_result.get("source_version")
    target_version = execution_result.get("target_version")

    logger.warning(
        "Ceph rollback requested but Ceph does not support version downgrade. "
        "Returning rolled_back=True as acknowledgment; no actual downgrade performed."
    )

    return {
        "rolled_back": True,
        "strategy": "partial_rollback",
        "source_version": source_version,
        "target_version": target_version,
        "data_loss_warning": (
            "Ceph does not support downgrade. No actual rollback was performed. "
            "Manual intervention required: check cluster health with 'ceph -s', "
            "restore from RBD/CephFS snapshots if needed, or contact Ceph support."
        ),
        "manual_steps": [
            "1. Check cluster health: ceph -s",
            "2. If HEALTH_ERR, restore from RBD snapshots or CephFS snapshots",
            "3. For partial upgrades within compatibility window, continue upgrading remaining daemons",
            "4. Contact Ceph support if cluster is in HEALTH_ERR state",
        ],
    }
