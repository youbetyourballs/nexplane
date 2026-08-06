# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
etcd cluster upgrade executor.
Rolling member upgrade: stop → replace binary → start → wait for member health.
Snapshot before upgrade; full snapshot restore on rollback (loses writes since snapshot).
Flow: preflight → etcd snapshot → rolling member upgrade → verify.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"


async def dispatch_agent_job(command, parameters, asset_ids, timeout_seconds=300):
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job as _fn
    return await _fn(command=command, parameters=parameters, asset_ids=asset_ids, timeout_seconds=timeout_seconds)


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    source_version = parameters.get("source_version")
    target_version = parameters.get("target_version")
    members = parameters.get("members", [])
    etcd_data_dir = parameters.get("etcd_data_dir", "/var/lib/etcd")
    snapshot_s3_bucket = parameters.get("snapshot_s3_bucket")
    dry_run = bool(parameters.get("dry_run", False))

    if not target_version:
        raise ValueError("target_version required")
    if not members:
        raise ValueError("members required — list of {name, peer_url, client_url}")

    # Phase 1: Preflight
    preflight = await dispatch_agent_job(
        command="preflight_etcd_upgrade",
        parameters={"source_version": source_version, "target_version": target_version, "members": members},
        asset_ids=[asset_id],
        timeout_seconds=180,
    )
    if preflight.get("status") == "preflight_blocked":
        return preflight

    if dry_run:
        return {"status": "dry_run", "target_version": target_version, "preflight": preflight}

    # Phase 2: Snapshot
    snapshot_path = "/tmp/nexplane-etcd-snapshot.db"
    snapshot = await dispatch_agent_job(
        command="etcd_snapshot_save",
        parameters={"snapshot_path": snapshot_path, "snapshot_s3_bucket": snapshot_s3_bucket, "members": members},
        asset_ids=[asset_id],
        timeout_seconds=300,
    )
    actual_snapshot_path = snapshot.get("snapshot_path", snapshot_path)

    # Phase 3: Rolling member upgrade
    members_upgraded = []
    for member in members:
        member_name = member.get("name")
        logger.info(f"Upgrading etcd member {member_name}")

        await dispatch_agent_job(
            command="etcd_replace_binary",
            parameters={"member": member, "target_version": target_version},
            asset_ids=[asset_id],
            timeout_seconds=300,
        )
        await dispatch_agent_job(
            command="etcd_wait_member_healthy",
            parameters={"member": member, "members": members},
            asset_ids=[asset_id],
            timeout_seconds=180,
        )
        members_upgraded.append(member_name)
        logger.info(f"etcd member {member_name} upgraded to {target_version}")

    # Phase 4: Verify
    verify = await dispatch_agent_job(
        command="etcd_verify_cluster",
        parameters={"members": members, "target_version": target_version},
        asset_ids=[asset_id],
        timeout_seconds=120,
    )

    return {
        "status": "completed" if verify.get("all_members_healthy") else "verify_failed",
        "source_version": source_version,
        "target_version": target_version,
        "members_upgraded": members_upgraded,
        "snapshot_path": actual_snapshot_path,
        "leader_id": verify.get("leader_id"),
        "verify_result": verify,
        "asset_id": asset_id,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_id = execution_result.get("asset_id")
    snapshot_path = execution_result.get("snapshot_path")

    if not snapshot_path:
        return {"rolled_back": False, "reason": "no snapshot_path in execution_result — cannot restore"}

    p = parameters.get("desired_outcome") or parameters
    members = p.get("members", [])
    etcd_data_dir = p.get("etcd_data_dir", "/var/lib/etcd")

    logger.info(f"Restoring etcd from snapshot {snapshot_path}")
    result = await dispatch_agent_job(
        command="etcd_snapshot_restore",
        parameters={
            "snapshot_path": snapshot_path,
            "members": members,
            "etcd_data_dir": etcd_data_dir,
            "source_version": execution_result.get("source_version"),
        },
        asset_ids=[asset_id],
        timeout_seconds=600,
    )

    return {
        "rolled_back": result.get("success", False),
        "strategy": "etcd_snapshot_restore",
        "snapshot_path": snapshot_path,
        "warning": "All writes after snapshot time are lost. Data loss window starts from snapshot creation.",
        "agent_result": result,
    }
