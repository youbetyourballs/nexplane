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
                cloud_instance_id = (asset.asset_metadata or {}).get("instance_id") if asset else None

            if cloud_instance_id:
                snapshot_meta = await _take_snapshot(asset_id, cloud_instance_id, connector)
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
            restore_status = await _restore_snapshot(asset_id, snapshot_id, snapshot_meta or {}, connector)
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
            restore_status = await _restore_snapshot(asset_id, snapshot_id, snapshot_meta or {}, connector)
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


async def _take_snapshot(asset_id: str, instance_id: str, connector) -> dict:
    """Take pre-upgrade EBS snapshot. Returns snapshot metadata dict."""
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

    reservations = ec2.describe_instances(InstanceIds=[instance_id])["Reservations"]
    if not reservations:
        raise RuntimeError(f"Instance {instance_id} not found")

    instance_data = reservations[0]["Instances"][0]
    az = instance_data["Placement"]["AvailabilityZone"]
    bdm = instance_data.get("BlockDeviceMappings", [])
    root_entry = next(
        (b for b in bdm if b.get("DeviceName") in ("/dev/xvda", "/dev/sda1", "/dev/nvme0n1p1")),
        None,
    )
    if not root_entry:
        raise RuntimeError("Could not identify root volume")
    root_vol_id = root_entry["Ebs"]["VolumeId"]
    root_device_name = root_entry["DeviceName"]

    snap = ec2.create_snapshot(
        VolumeId=root_vol_id,
        Description=f"Pre-OS-upgrade snapshot for {asset_id} at {datetime.now(timezone.utc).isoformat()}",
        TagSpecifications=[{"ResourceType": "snapshot", "Tags": [
            {"Key": "nexplane-purpose", "Value": "pre-os-upgrade"},
            {"Key": "nexplane-asset-id", "Value": asset_id},
        ]}],
    )
    return {
        "snapshot_id": snap["SnapshotId"],
        "root_volume_id": root_vol_id,
        "root_device_name": root_device_name,
        "availability_zone": az,
        "region": region,
        "instance_id": instance_id,
    }


async def _restore_snapshot(
    asset_id: str,
    snapshot_id: str,
    snapshot_meta: dict,
    connector,
) -> dict:
    """Automated EBS root volume swap: stop → create-from-snap → detach → attach → start → verify agent."""
    instance_id = snapshot_meta.get("instance_id")
    root_volume_id = snapshot_meta.get("root_volume_id")
    root_device_name = snapshot_meta.get("root_device_name", "/dev/xvda")
    az = snapshot_meta.get("availability_zone")
    region = snapshot_meta.get("region", "us-east-1")

    if not instance_id or not root_volume_id or not az:
        raise RuntimeError(f"Incomplete snapshot_meta for restore: {snapshot_meta}")

    creds = getattr(connector, "credentials", {}) or {}
    if not creds.get("access_key_id"):
        raise RuntimeError("No cloud credentials available for EBS restore")

    import boto3
    ec2 = boto3.client(
        "ec2",
        aws_access_key_id=creds["access_key_id"],
        aws_secret_access_key=creds["secret_access_key"],
        region_name=region,
    )
    loop = asyncio.get_event_loop()

    # 1. Stop instance
    logger.info(f"Stopping instance {instance_id} for EBS restore")
    ec2.stop_instances(InstanceIds=[instance_id])
    await loop.run_in_executor(
        None,
        lambda: ec2.get_waiter("instance_stopped").wait(
            InstanceIds=[instance_id],
            WaiterConfig={"Delay": 15, "MaxAttempts": 20},
        ),
    )
    logger.info(f"Instance {instance_id} stopped")

    # 2. Create new volume from snapshot in same AZ
    logger.info(f"Creating volume from snapshot {snapshot_id} in {az}")
    new_vol = ec2.create_volume(
        SnapshotId=snapshot_id,
        AvailabilityZone=az,
        VolumeType="gp3",
        TagSpecifications=[{"ResourceType": "volume", "Tags": [
            {"Key": "nexplane-purpose", "Value": "os-upgrade-restore"},
            {"Key": "nexplane-asset-id", "Value": asset_id},
        ]}],
    )
    new_vol_id = new_vol["VolumeId"]
    await loop.run_in_executor(
        None,
        lambda: ec2.get_waiter("volume_available").wait(
            VolumeIds=[new_vol_id],
            WaiterConfig={"Delay": 10, "MaxAttempts": 30},
        ),
    )
    logger.info(f"New volume {new_vol_id} ready")

    # 3. Detach current root volume
    logger.info(f"Detaching current root volume {root_volume_id}")
    try:
        ec2.detach_volume(VolumeId=root_volume_id, InstanceId=instance_id, Force=False)
        await loop.run_in_executor(
            None,
            lambda: ec2.get_waiter("volume_available").wait(
                VolumeIds=[root_volume_id],
                WaiterConfig={"Delay": 10, "MaxAttempts": 30},
            ),
        )
    except Exception as e:
        logger.warning(f"Detach of {root_volume_id} raised: {e} — proceeding")

    # Tag old volume for operator cleanup — do NOT delete
    try:
        ec2.create_tags(
            Resources=[root_volume_id],
            Tags=[
                {"Key": "nexplane-rollback-orphan", "Value": "true"},
                {"Key": "nexplane-asset-id", "Value": asset_id},
            ],
        )
    except Exception:
        pass

    # 4. Attach new volume as root
    logger.info(f"Attaching {new_vol_id} as {root_device_name} on {instance_id}")
    ec2.attach_volume(VolumeId=new_vol_id, InstanceId=instance_id, Device=root_device_name)
    await loop.run_in_executor(
        None,
        lambda: ec2.get_waiter("volume_in_use").wait(
            VolumeIds=[new_vol_id],
            WaiterConfig={"Delay": 5, "MaxAttempts": 24},
        ),
    )

    # 5. Start instance
    logger.info(f"Starting instance {instance_id}")
    ec2.start_instances(InstanceIds=[instance_id])
    await loop.run_in_executor(
        None,
        lambda: ec2.get_waiter("instance_running").wait(
            InstanceIds=[instance_id],
            WaiterConfig={"Delay": 10, "MaxAttempts": 30},
        ),
    )
    logger.info(f"Instance {instance_id} running — waiting for agent heartbeat")

    # 6. Poll agent heartbeat until online or 300s timeout
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    agent_recovered = False
    deadline = asyncio.get_event_loop().time() + 300
    while asyncio.get_event_loop().time() < deadline:
        try:
            await dispatch_agent_job(
                command="health_check",
                parameters={},
                asset_ids=[asset_id],
                timeout_seconds=20,
            )
            agent_recovered = True
            logger.info(f"Agent on {asset_id} recovered after EBS restore")
            break
        except Exception:
            await asyncio.sleep(30)

    # Tag snapshot as used
    try:
        ec2.create_tags(
            Resources=[snapshot_id],
            Tags=[{"Key": "nexplane-rollback-used", "Value": "true"}],
        )
    except Exception:
        pass

    return {
        "restored": agent_recovered,
        "new_volume_id": new_vol_id,
        "old_volume_id": root_volume_id,
        "instance_id": instance_id,
        "agent_recovered": agent_recovered,
    }


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

    try:
        result = await _restore_snapshot(asset_id, snapshot_id, snapshot_meta, connector)
    except Exception as exc:
        return {"rolled_back": False, "reason": str(exc), "snapshot_id": snapshot_id}

    return {
        "rolled_back": result["restored"],
        "snapshot_id": snapshot_id,
        "new_volume_id": result.get("new_volume_id"),
        "old_volume_id": result.get("old_volume_id"),
        "agent_recovered": result.get("agent_recovered"),
    }
