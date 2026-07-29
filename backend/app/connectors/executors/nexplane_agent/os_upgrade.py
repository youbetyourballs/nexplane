# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
OS major version upgrade executor.
Flow: preflight -> snapshot -> upgrade -> verify -> (auto-rollback on failure).
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone

from app.connectors.executors.nexplane_agent._snapshot_helpers import (
    _get_aws_creds,
    _make_ec2_client,
    _restore_snapshot,
    _take_snapshot,
)

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    dry_run = bool(parameters.get("dry_run", False))
    skip_snapshot = bool(parameters.get("skip_snapshot", False))
    target_version = parameters.get("target_version", "")
    snapshot_only = bool(parameters.get("snapshot_only", False))

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    from app.database import AsyncSessionLocal
    from app.models.asset import Asset

    # Step 1: Pre-flight check (skip if snapshot_only)
    preflight = None
    if not snapshot_only:
        logger.info(f"OS upgrade preflight for asset {asset_id}")
        preflight = await dispatch_agent_job(
            command="preflight_os_upgrade",
            parameters={"target_version": target_version, "dry_run": dry_run},
            asset_ids=[asset_id],
            timeout_seconds=120,
        )

        if preflight.get("status") == "blocked":
            return {
                "status": "blocked",
                "reason": preflight.get("reason", "Pre-flight checks failed"),
                "preflight": preflight,
            }

        if dry_run:
            return {
                "status": "dry_run",
                "current_os": preflight.get("current_os"),
                "target_os": preflight.get("target_os"),
                "packages_to_migrate": preflight.get("packages_to_migrate", []),
                "estimated_duration_minutes": preflight.get("estimated_duration_minutes", 60),
                "warnings": preflight.get("warnings", []),
            }

    # Step 2: Take snapshot before upgrade
    snapshot_meta = None
    snapshot_id = None
    if not skip_snapshot:
        logger.info(f"Taking pre-upgrade snapshot for {asset_id}")
        try:
            async with AsyncSessionLocal() as db:
                asset = await db.get(Asset, uuid.UUID(asset_id))
                meta = asset.asset_metadata or {} if asset else {}
                cloud_instance_id = meta.get("instance_id")
                organization_id = asset.organization_id if asset else None

            # Agent-registered assets don't store instance_id; look it up from private IP via AWS
            if not cloud_instance_id and asset:
                cloud_instance_id = await _lookup_instance_id_by_ip(meta, connector, organization_id)

            if cloud_instance_id:
                snapshot_meta = await _take_snapshot(asset_id, cloud_instance_id, connector, organization_id)
                snapshot_id = snapshot_meta["snapshot_id"]
                logger.info(f"Snapshot created: {snapshot_id}")
        except Exception as e:
            logger.warning(f"Snapshot failed (proceeding without): {e}")
            cloud_meta = {}
            async with AsyncSessionLocal() as db:
                asset = await db.get(Asset, uuid.UUID(asset_id))
                if asset:
                    cloud_meta = asset.asset_metadata or {}
            if cloud_meta.get("environment") == "prod" and not skip_snapshot:
                raise RuntimeError(f"Pre-upgrade snapshot failed on prod asset: {e}. Set skip_snapshot=true to override.")

    if snapshot_only:
        return {
            "status": "snapshot_only",
            "snapshot_id": snapshot_meta["snapshot_id"] if snapshot_meta else None,
            "snapshot_meta": snapshot_meta,
            "asset_id": asset_id,
        }

    # Step 3: Execute upgrade
    logger.info(f"Starting OS upgrade for {asset_id}, target={target_version}")
    try:
        upgrade_result = await dispatch_agent_job(
            command="execute_os_upgrade",
            parameters={
                "target_version": target_version,
                "snapshot_id": snapshot_id or "",
            },
            asset_ids=[asset_id],
            timeout_seconds=7200,  # 2 hours — OS upgrades take time
        )
    except Exception as e:
        logger.error(f"OS upgrade failed for {asset_id}: {e}")
        if snapshot_id:
            logger.info(f"Auto-rolling back to snapshot {snapshot_id}")
            restore_status = await _restore_snapshot(asset_id, snapshot_id, snapshot_meta or {}, connector, organization_id=None)
            return {
                "status": "failed_and_rolled_back",
                "error": str(e),
                "snapshot_id": snapshot_id,
                "snapshot_meta": snapshot_meta,
                "rollback_status": restore_status,
            }
        raise

    # Step 4: Verify upgrade
    logger.info(f"Verifying OS upgrade for {asset_id}")
    try:
        # Wait for agent to come back online after reboot
        await asyncio.sleep(60)
        verify_result = await dispatch_agent_job(
            command="verify_os_upgrade",
            parameters={
                "target_version": target_version,
                "pre_upgrade_services": (preflight or {}).get("running_services", []),
            },
            asset_ids=[asset_id],
            timeout_seconds=300,
        )
    except Exception as e:
        logger.warning(f"Verification failed after upgrade: {e}")
        verify_result = {"verified": False, "error": str(e)}

    if not verify_result.get("verified", True):
        if snapshot_id:
            logger.warning(f"Verification failed — restoring snapshot {snapshot_id}")
            restore_status = await _restore_snapshot(asset_id, snapshot_id, snapshot_meta or {}, connector, organization_id=None)
            return {
                "status": "failed_and_rolled_back",
                "verify_result": verify_result,
                "snapshot_id": snapshot_id,
                "snapshot_meta": snapshot_meta,
                "rollback_status": restore_status,
            }

    # Update asset metadata with new OS version
    try:
        async with AsyncSessionLocal() as db:
            asset = await db.get(Asset, uuid.UUID(asset_id))
            if asset:
                meta = dict(asset.asset_metadata or {})
                meta["os_version"] = verify_result.get("new_os_version", target_version)
                meta["os_upgrade_at"] = datetime.now(timezone.utc).isoformat()
                meta["last_security_patch_at"] = datetime.now(timezone.utc).isoformat()
                asset.asset_metadata = meta
                await db.commit()
    except Exception as e:
        logger.warning(f"Failed to update asset metadata after upgrade: {e}")

    return {
        "status": "completed",
        "previous_os": (preflight or {}).get("current_os"),
        "new_os": verify_result.get("new_os_version"),
        "snapshot_id": snapshot_id,
        "snapshot_meta": snapshot_meta,
        "upgrade_result": upgrade_result,
        "verify_result": verify_result,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def _lookup_instance_id_by_ip(asset_meta: dict, connector, organization_id=None) -> str:
    """Look up EC2 instance_id from the asset's private IP using the AWS connector."""
    ip_addresses = asset_meta.get("ip_addresses") or []
    private_ip = next((ip for ip in ip_addresses if ip.startswith("172.") or ip.startswith("10.")), None)
    if not private_ip:
        return ""
    try:
        creds = await _get_aws_creds(connector, organization_id)
        ec2 = _make_ec2_client(creds)
        reservations = ec2.describe_instances(
            Filters=[{"Name": "private-ip-address", "Values": [private_ip]}]
        )["Reservations"]
        if reservations:
            return reservations[0]["Instances"][0]["InstanceId"]
    except Exception as e:
        logger.warning(f"Could not look up instance_id for IP {private_ip}: {e}")
    return ""










async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    snapshot_id = execution_result.get("snapshot_id")
    snapshot_meta = execution_result.get("snapshot_meta") or {}
    asset_id = execution_result.get("asset_id") or (
        str(parameters.get("asset_ids", [None])[0]) if parameters.get("asset_ids") else None
    )

    if not snapshot_id:
        return {"rolled_back": False, "reason": "no_snapshot_available"}

    if not snapshot_meta.get("instance_id"):
        return {
            "rolled_back": False,
            "reason": "no_snapshot_meta — CR predates automated rollback; restore manually",
            "snapshot_id": snapshot_id,
            "manual_steps": [
                "1. Stop the EC2 instance",
                f"2. aws ec2 create-volume --snapshot-id {snapshot_id} --availability-zone <az>",
                "3. Detach current root volume",
                "4. Attach new volume as root device",
                "5. Start instance",
            ],
        }

    organization_id = None
    if asset_id:
        from app.database import AsyncSessionLocal
        from app.models.asset import Asset
        try:
            async with AsyncSessionLocal() as db:
                asset = await db.get(Asset, uuid.UUID(asset_id))
                organization_id = asset.organization_id if asset else None
        except Exception:
            pass

    try:
        result = await _restore_snapshot(asset_id, snapshot_id, snapshot_meta, connector, organization_id)
    except Exception as exc:
        return {"rolled_back": False, "reason": str(exc), "snapshot_id": snapshot_id}

    return {
        "rolled_back": result["restored"],
        "snapshot_id": snapshot_id,
        "new_volume_id": result.get("new_volume_id"),
        "old_volume_id": result.get("old_volume_id"),
        "agent_recovered": result.get("agent_recovered"),
    }
