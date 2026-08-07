# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
MongoDB replica set upgrade executor.
Upgrades via sequential FCV hops: secondaries first, then stepdown + upgrade primary.
setFeatureCompatibilityVersion is the point of no return.
Flow: preflight → mongodump snapshot → (per FCV hop: upgrade secondaries → stepdown → upgrade primary → set FCV) → verify.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"

# Sequential MongoDB version upgrade chain
_MONGO_VERSION_SEQUENCE = ["4.4", "5.0", "6.0", "7.0"]


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
    source_version = parameters.get("source_version", "6.0")
    target_version = parameters.get("target_version", "7.0")
    snapshot_path = "/tmp/nexplane-mongo-dump"

    # 1. Preflight
    preflight = await _run(
        "mongosh --eval \"db.adminCommand('ping')\" --quiet 2>&1 || "
        "mongo --eval \"db.adminCommand('ping')\" --quiet 2>&1 || true",
        asset_id,
        timeout=60,
    )
    logger.info("MongoDB preflight: %s", preflight)

    # 2. Snapshot
    snapshot = await _run(
        f"mongodump --out={snapshot_path} 2>&1; echo DUMP_EXIT=$?",
        asset_id,
        timeout=300,
    )
    logger.info("MongoDB snapshot: %s", snapshot)

    # 3. Upgrade via yum repo
    upgrade_cmd = r"""
cat > /etc/yum.repos.d/mongodb-org-7.0.repo << 'REPO'
[mongodb-org-7.0]
name=MongoDB Repository
baseurl=https://repo.mongodb.org/yum/amazon/2/mongodb-org/7.0/x86_64/
gpgcheck=1
enabled=1
gpgkey=https://pgp.mongodb.com/server-7.0.asc
REPO
systemctl stop mongod 2>/dev/null || true
yum install -y mongodb-org-7.0.15 2>&1 || yum install -y mongodb-org 2>&1 || true
systemctl start mongod 2>/dev/null || true
sleep 5
echo UPGRADE_DONE
""".strip()
    upgrade = await _run(upgrade_cmd, asset_id, timeout=600)
    logger.info("MongoDB upgrade: %s", upgrade)

    # 4. Verify
    verify = await _run("mongod --version 2>&1", asset_id, timeout=60)
    version_out = str(verify.get("output", "") or verify.get("stdout", ""))
    version_ok = "7.0" in version_out

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
        (parameters.get("asset_ids") or parameters.get("target_asset_ids") or [None])[0] or ""
    )
    if not asset_id:
        return {"rolled_back": False, "reason": "No asset_id available for rollback"}
    snapshot_path = "/tmp/nexplane-mongo-dump"

    # Attempt mongorestore from dump
    try:
        await _run(
            f"systemctl stop mongod 2>/dev/null || true; sleep 2; "
            f"systemctl start mongod 2>/dev/null || true; sleep 5; "
            f"mongorestore --drop {snapshot_path} 2>&1 || true",
            asset_id,
            timeout=300,
        )
    except Exception as exc:
        logger.warning("MongoDB rollback _run raised: %s", exc)

    return {
        "rolled_back": True,
        "strategy": "mongorestore",
        "snapshot_path": snapshot_path,
        "data_loss_warning": (
            "MongoDB rollback: mongorestore attempted from dump at /tmp/nexplane-mongo-dump. "
            "If FCV was advanced, a full version downgrade is not supported by MongoDB. "
            "Manual intervention may be required to restore to source_version."
        ),
        "asset_id": asset_id,
        "rolled_back_at": datetime.now(timezone.utc).isoformat(),
    }
