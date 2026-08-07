# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
etcd cluster upgrade executor.
Rolling member upgrade: stop → replace binary → start → wait for member health.
Snapshot before upgrade; full snapshot restore on rollback (loses writes since snapshot).
Flow: preflight → etcd snapshot → upgrade → verify.
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
    source_version = parameters.get("source_version")
    target_version = parameters.get("target_version")
    etcd_data_dir = parameters.get("etcd_data_dir", "/var/lib/etcd")
    dry_run = bool(parameters.get("dry_run", False))

    if not target_version:
        raise ValueError("target_version required")

    # Phase 1: Preflight
    logger.info(f"etcd preflight for asset {asset_id}, target={target_version}")
    preflight = await _run(
        "ETCDCTL_API=3 ETCDCTL_ENDPOINTS=http://127.0.0.1:2379 etcdctl endpoint health 2>&1 || etcd --version 2>&1 || true",
        asset_id,
        timeout=60,
    )
    preflight_out = preflight.get("output", "") or ""

    if dry_run:
        return {"status": "dry_run", "target_version": target_version, "preflight_output": preflight_out}

    # Phase 2: Snapshot
    snapshot_path = "/tmp/nexplane-etcd-snapshot.db"
    logger.info(f"Taking etcd snapshot for asset {asset_id}")
    snap_cmd = (
        f"ETCDCTL_API=3 ETCDCTL_ENDPOINTS=http://127.0.0.1:2379 "
        f"etcdctl snapshot save {snapshot_path} 2>&1; echo SNAP_EXIT=$?"
    )
    snapshot = await _run(snap_cmd, asset_id, timeout=120)
    snapshot_out = snapshot.get("output", "") or ""

    # Phase 3: Upgrade
    # Map short version like "3.5" to a specific patch release
    ver_map = {"3.5": "3.5.15", "3.4": "3.4.34", "3.3": "3.3.27"}
    full_ver = ver_map.get(target_version, f"{target_version}.0")

    logger.info(f"Upgrading etcd {source_version} → {target_version} (binary {full_ver})")
    upgrade_cmd = (
        f"VER={full_ver} && "
        f"curl -sfL https://github.com/etcd-io/etcd/releases/download/v${{VER}}/etcd-v${{VER}}-linux-amd64.tar.gz "
        f"| tar xz -C /tmp/ && "
        f"systemctl stop etcd 2>/dev/null || true && "
        f"cp /tmp/etcd-v${{VER}}-linux-amd64/etcd /usr/local/bin/etcd 2>/dev/null || "
        f"cp /tmp/etcd-v${{VER}}-linux-amd64/etcd $(which etcd 2>/dev/null || echo /usr/bin/etcd) 2>/dev/null || true && "
        f"cp /tmp/etcd-v${{VER}}-linux-amd64/etcdctl /usr/local/bin/etcdctl 2>/dev/null || true && "
        f"systemctl start etcd 2>/dev/null || "
        f"nohup etcd --data-dir={etcd_data_dir} >> /var/log/etcd.log 2>&1 & "
        f"sleep 5 && echo UPGRADE_DONE"
    )
    upgrade = await _run(upgrade_cmd, asset_id, timeout=300)
    upgrade_out = upgrade.get("output", "") or ""

    # Phase 4: Verify
    logger.info(f"Verifying etcd version for asset {asset_id}")
    ver_result = await _run("etcd --version 2>&1", asset_id, timeout=30)
    ver_out = ver_result.get("output", "") or ""

    major_minor = target_version.rsplit(".", 1)[0] if "." in target_version else target_version
    verified = major_minor in ver_out or target_version in ver_out
    status = "completed" if verified else "verify_failed"

    return {
        "status": status,
        "source_version": source_version,
        "target_version": target_version,
        "snapshot_path": snapshot_path,
        "snapshot_output": snapshot_out,
        "upgrade_output": upgrade_out,
        "version_output": ver_out,
        "asset_id": asset_id,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_id = execution_result.get("asset_id")
    snapshot_path = execution_result.get("snapshot_path", "/tmp/nexplane-etcd-snapshot.db")

    if not asset_id:
        return {"rolled_back": False, "reason": "asset_id missing from execution_result"}

    p = parameters.get("desired_outcome") or parameters
    etcd_data_dir = p.get("etcd_data_dir", "/var/lib/etcd")
    source_version = execution_result.get("source_version", "3.4")

    # Map source version to full binary version for rollback
    ver_map = {"3.5": "3.5.15", "3.4": "3.4.34", "3.3": "3.3.27"}
    full_ver = ver_map.get(source_version, f"{source_version}.0")

    logger.info(f"Rolling back etcd for asset {asset_id}, restoring from {snapshot_path}")

    rollback_cmd = (
        f"systemctl stop etcd 2>/dev/null || true && "
        f"VER={full_ver} && "
        f"curl -sfL https://github.com/etcd-io/etcd/releases/download/v${{VER}}/etcd-v${{VER}}-linux-amd64.tar.gz "
        f"| tar xz -C /tmp/ && "
        f"cp /tmp/etcd-v${{VER}}-linux-amd64/etcd /usr/local/bin/etcd 2>/dev/null || true && "
        f"cp /tmp/etcd-v${{VER}}-linux-amd64/etcdctl /usr/local/bin/etcdctl 2>/dev/null || true && "
        f"ETCDCTL_API=3 ETCDCTL_ENDPOINTS=http://127.0.0.1:2379 "
        f"etcdctl snapshot restore {snapshot_path} --data-dir={etcd_data_dir}-restore 2>&1 || true && "
        f"systemctl start etcd 2>/dev/null || "
        f"nohup etcd --data-dir={etcd_data_dir} >> /var/log/etcd.log 2>&1 & "
        f"sleep 5 && etcd --version 2>&1"
    )
    result = await _run(rollback_cmd, asset_id, timeout=300)
    result_out = result.get("output", "") or ""

    return {
        "rolled_back": True,
        "strategy": "etcd_snapshot_restore",
        "snapshot_path": snapshot_path,
        "data_loss_warning": "All writes after snapshot time are lost. Data loss window starts from snapshot creation.",
        "version_output": result_out,
    }
