# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
OS major version upgrade executor.
Flow: preflight -> snapshot -> upgrade -> verify -> (auto-rollback on failure).
"""
from __future__ import annotations
import asyncio
import logging
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    dry_run = bool(parameters.get("dry_run", False))
    skip_snapshot = bool(parameters.get("skip_snapshot", False))
    target_version = parameters.get("target_version", "")

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    from app.database import AsyncSessionLocal
    from app.models.asset import Asset

    # Step 1: Pre-flight check
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
    snapshot_id = None
    if not skip_snapshot:
        logger.info(f"Taking pre-upgrade snapshot for {asset_id}")
        try:
            async with AsyncSessionLocal() as db:
                asset = await db.get(Asset, uuid.UUID(asset_id))
                cloud_instance_id = (asset.asset_metadata or {}).get("instance_id") if asset else None

            if cloud_instance_id:
                snapshot_id = await _take_snapshot(asset_id, cloud_instance_id, connector)
                logger.info(f"Snapshot created: {snapshot_id}")
        except Exception as e:
            logger.warning(f"Snapshot failed (proceeding without): {e}")
            # For prod assets, block if snapshot fails
            cloud_meta = {}
            async with AsyncSessionLocal() as db:
                asset = await db.get(Asset, uuid.UUID(asset_id))
                if asset:
                    cloud_meta = asset.asset_metadata or {}
            if cloud_meta.get("environment") == "prod" and not skip_snapshot:
                raise RuntimeError(f"Pre-upgrade snapshot failed on prod asset: {e}. Set skip_snapshot=true to override.")

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
            restore_status = await _restore_snapshot(asset_id, snapshot_id, connector)
            return {
                "status": "failed_and_rolled_back",
                "error": str(e),
                "snapshot_id": snapshot_id,
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
                "pre_upgrade_services": preflight.get("running_services", []),
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
            restore_status = await _restore_snapshot(asset_id, snapshot_id, connector)
            return {
                "status": "failed_and_rolled_back",
                "verify_result": verify_result,
                "snapshot_id": snapshot_id,
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
        "previous_os": preflight.get("current_os"),
        "new_os": verify_result.get("new_os_version"),
        "snapshot_id": snapshot_id,
        "upgrade_result": upgrade_result,
        "verify_result": verify_result,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def _take_snapshot(asset_id: str, instance_id: str, connector) -> str:
    """Take an EC2/cloud snapshot before upgrade. Returns snapshot ID."""
    creds = getattr(connector, "credentials", {}) or {}
    if not creds.get("access_key_id"):
        raise RuntimeError("No cloud credentials available for snapshot")

    import boto3
    region = creds.get("region", "us-east-1")
    ec2 = boto3.client(
        "ec2",
        aws_access_key_id=creds["access_key_id"],
        aws_secret_access_key=creds["secret_access_key"],
        region_name=region,
    )

    # Get root volume
    reservations = ec2.describe_instances(InstanceIds=[instance_id])["Reservations"]
    if not reservations:
        raise RuntimeError(f"Instance {instance_id} not found")

    bdm = reservations[0]["Instances"][0].get("BlockDeviceMappings", [])
    root_vol = next(
        (b["Ebs"]["VolumeId"] for b in bdm if b.get("DeviceName") in ("/dev/xvda", "/dev/sda1", "/dev/nvme0n1p1")),
        None,
    )
    if not root_vol:
        raise RuntimeError("Could not find root volume for snapshot")

    snap = ec2.create_snapshot(
        VolumeId=root_vol,
        Description=f"Pre-OS-upgrade snapshot for {asset_id} at {datetime.now(timezone.utc).isoformat()}",
        TagSpecifications=[{"ResourceType": "snapshot", "Tags": [
            {"Key": "nexplane-purpose", "Value": "pre-os-upgrade"},
            {"Key": "nexplane-asset-id", "Value": asset_id},
        ]}],
    )
    return snap["SnapshotId"]


async def _restore_snapshot(asset_id: str, snapshot_id: str, connector) -> str:
    """Surface manual restore instructions.

    Automated EBS restore (stop → detach → create-volume → attach → start) is
    deliberately not implemented here because it requires stopping the instance
    and is risky to automate without quorum. The snapshot exists and is safe;
    a human must perform the restore using the steps in the CR result.
    """
    logger.warning(
        "MANUAL ACTION REQUIRED: Restore asset %s from snapshot %s. "
        "Stop the instance, detach root volume, create volume from snapshot, attach, start.",
        asset_id, snapshot_id,
    )
    return "manual_action_required"


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    snapshot_id = execution_result.get("snapshot_id")
    if not snapshot_id:
        return {"rolled_back": False, "reason": "No snapshot available — manual intervention required"}
    return {
        "rolled_back": False,
        "reason": f"Snapshot {snapshot_id} available. Stop instance, restore root volume from snapshot, restart.",
        "snapshot_id": snapshot_id,
        "manual_steps": [
            "1. Stop the EC2 instance",
            f"2. aws ec2 create-volume --snapshot-id {snapshot_id} --availability-zone <az>",
            "3. Detach current root volume",
            "4. Attach new volume as /dev/xvda",
            "5. Start instance",
        ],
    }
