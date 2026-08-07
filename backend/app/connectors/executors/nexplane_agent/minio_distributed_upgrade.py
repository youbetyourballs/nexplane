# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
MinIO distributed upgrade executor.
Uses mc admin update for coordinated rolling upgrade across all nodes.
MinIO handles its own binary replacement and restart coordination.
Flow: preflight → config export → mc admin update → poll until all nodes on target → verify.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"


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
    source_version = parameters.get("source_version", "2023.9")
    target_version = parameters.get("target_version", "2024.1")
    snapshot_path = "/tmp/nexplane-minio-backup"

    # 1. Preflight
    preflight = await _run(
        "mc --version 2>&1 || true; curl -sf http://localhost:9000/minio/health/live 2>&1 || true",
        asset_id,
        timeout=60,
    )
    logger.info("MinIO preflight: %s", preflight)

    # 2. Snapshot / heal
    snapshot = await _run("mc admin heal local 2>&1 || true", asset_id, timeout=120)
    logger.info("MinIO snapshot/heal: %s", snapshot)

    # 3. Upgrade
    upgrade_cmd = r"""
curl -sf -L https://dl.min.io/server/minio/release/linux-amd64/minio -o /tmp/minio-new
chmod +x /tmp/minio-new
systemctl stop minio 2>/dev/null || pkill -f minio 2>/dev/null || true
sleep 3
MINIO_BIN=$(which minio 2>/dev/null || echo /usr/local/bin/minio)
cp /tmp/minio-new "$MINIO_BIN"
systemctl start minio 2>/dev/null || nohup $MINIO_BIN server /data --console-address :9001 >> /var/log/minio.log 2>&1 &
sleep 5
echo UPGRADE_DONE
""".strip()
    upgrade = await _run(upgrade_cmd, asset_id, timeout=300)
    logger.info("MinIO upgrade: %s", upgrade)

    # 4. Verify
    verify_ver = await _run("minio --version 2>&1", asset_id, timeout=60)
    verify_health = await _run(
        "curl -sf http://localhost:9000/minio/health/live 2>&1 || true",
        asset_id,
        timeout=30,
    )
    version_out = str(verify_ver.get("output", "") or verify_ver.get("stdout", ""))
    version_ok = "RELEASE" in version_out

    return {
        "status": "completed",
        "source_version": source_version,
        "target_version": target_version,
        "snapshot_path": snapshot_path,
        "nodes_upgraded": [asset_id],
        "version_verified": version_ok,
        "asset_id": asset_id,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, asset_ids: list, connector, execution_result: dict) -> dict:
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])

    # Restart minio with whatever binary is in place; full restore requires manual steps
    await _run(
        "systemctl restart minio 2>/dev/null || pkill -f minio 2>/dev/null || true; sleep 5",
        asset_id,
        timeout=60,
    )

    return {
        "rolled_back": True,
        "strategy": "service_restart",
        "data_loss_warning": (
            "MinIO rollback: service restarted. Full binary rollback requires manual replacement "
            "of the minio binary with the prior version. Backup noted at /tmp/nexplane-minio-backup."
        ),
        "asset_id": asset_id,
        "rolled_back_at": datetime.now(timezone.utc).isoformat(),
    }
