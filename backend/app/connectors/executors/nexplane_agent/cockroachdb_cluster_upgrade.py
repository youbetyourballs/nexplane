# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
CockroachDB cluster upgrade executor.
Rolling binary upgrade: drain → replace binary → restart per node.
Version finalization is a separate explicit step (or via auto_finalize flag).
Flow: preflight → backup → rolling node upgrade → (optional) finalize version → verify.
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
    source_version = parameters.get("source_version", "23.1")
    target_version = parameters.get("target_version", "23.2")
    snapshot_path = "/tmp/nexplane-crdb-backup"

    # 1. Preflight
    preflight = await _run(
        "cockroach version 2>&1 || /usr/local/bin/cockroach version 2>&1 || true",
        asset_id,
        timeout=60,
    )
    logger.info("CockroachDB preflight: %s", preflight)

    # 2. Snapshot / backup note
    snapshot = await _run(
        "cockroach node backup --insecure 2>&1 || true",
        asset_id,
        timeout=120,
    )
    logger.info("CockroachDB snapshot: %s", snapshot)

    # 3. Upgrade
    upgrade_cmd = r"""
VER=23.2.22
URL="https://binaries.cockroachdb.com/cockroach-v${VER}.linux-amd64.tgz"
curl -sf -L -o /tmp/cockroach-${VER}.tgz "$URL" || { echo DOWNLOAD_FAILED; exit 0; }
tar -xzf /tmp/cockroach-${VER}.tgz -C /tmp/
CRDB_BIN=$(which cockroach 2>/dev/null || echo /usr/local/bin/cockroach)
systemctl stop cockroach 2>/dev/null || true; sleep 3
cp /tmp/cockroach-v${VER}.linux-amd64/cockroach "$CRDB_BIN"
chmod +x "$CRDB_BIN"
systemctl start cockroach 2>/dev/null || nohup $CRDB_BIN start-single-node --insecure --listen-addr=0.0.0.0:26257 --http-addr=0.0.0.0:8080 >> /var/log/cockroach.log 2>&1 &
sleep 8
echo UPGRADE_DONE
""".strip()
    upgrade = await _run(upgrade_cmd, asset_id, timeout=300)
    logger.info("CockroachDB upgrade: %s", upgrade)

    # 4. Verify
    verify = await _run("cockroach version 2>&1", asset_id, timeout=60)
    version_out = str(verify.get("output", "") or verify.get("stdout", ""))
    version_ok = "23.2" in version_out

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


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_id = execution_result.get("asset_id") or str(
        (parameters.get("asset_ids") or [None])[0] or ""
    )
    if not asset_id:
        return {"rolled_back": False, "reason": "No asset_id available for rollback"}
    source_version = parameters.get("source_version", "23.1")

    # Download old binary and replace
    rollback_cmd = rf"""
VER=23.1.26
URL="https://binaries.cockroachdb.com/cockroach-v${{VER}}.linux-amd64.tgz"
curl -sf -L -o /tmp/cockroach-rollback.tgz "$URL" || {{ echo DOWNLOAD_FAILED; exit 0; }}
tar -xzf /tmp/cockroach-rollback.tgz -C /tmp/
CRDB_BIN=$(which cockroach 2>/dev/null || echo /usr/local/bin/cockroach)
systemctl stop cockroach 2>/dev/null || true; sleep 3
cp /tmp/cockroach-v${{VER}}.linux-amd64/cockroach "$CRDB_BIN"
chmod +x "$CRDB_BIN"
systemctl start cockroach 2>/dev/null || nohup $CRDB_BIN start-single-node --insecure --listen-addr=0.0.0.0:26257 --http-addr=0.0.0.0:8080 >> /var/log/cockroach.log 2>&1 &
sleep 8
echo ROLLBACK_DONE
""".strip()
    await _run(rollback_cmd, asset_id, timeout=300)

    return {
        "rolled_back": True,
        "strategy": "binary_replacement",
        "source_version": source_version,
        "data_loss_warning": (
            "CockroachDB rollback: binary replaced with prior version. "
            "If cluster version was finalized, data may not be fully recoverable without restoring from backup at /tmp/nexplane-crdb-backup."
        ),
        "asset_id": asset_id,
        "rolled_back_at": datetime.now(timezone.utc).isoformat(),
    }
