# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Snapshot helper functions for OS upgrade operations.
"""
import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


async def _get_aws_creds(connector, organization_id=None) -> dict:
    """Return AWS credentials — from connector if present, otherwise from the AWS connector in DB."""
    try:
        creds = connector.credentials or {}
        if isinstance(creds, dict) and creds.get("access_key_id"):
            return creds
    except Exception:
        pass
    from app.database import AsyncSessionLocal
    from app.models.connector import Connector
    from app.models.connector_credential import ConnectorCredential
    from app.services.secret_backend_factory import get_secret_backend
    from sqlalchemy import select
    async with AsyncSessionLocal() as db:
        stmt = (
            select(ConnectorCredential)
            .join(Connector, Connector.id == ConnectorCredential.connector_id)
            .where(Connector.connector_type == "aws")
        )
        if organization_id:
            stmt = stmt.where(Connector.organization_id == organization_id)
        result = await db.execute(stmt)
        rows = result.scalars().all()
        backend = get_secret_backend()
        # Prefer permanent IAM user keys (AKIA prefix) over STS temporary keys (ASIA prefix)
        permanent = None
        fallback = None
        for row in rows:
            if not row.credentials_encrypted:
                continue
            try:
                creds = backend.decrypt_json(row.credentials_encrypted)
            except Exception:
                continue
            key = creds.get("access_key_id", "")
            if key.startswith("AKIA"):
                permanent = creds
                break
            if fallback is None:
                fallback = creds
        return permanent or fallback or {}
    return {}


def _make_ec2_client(creds: dict):
    """Create a boto3 EC2 client using stored creds; instance profile handles auth if stored creds expired."""
    import boto3
    region = creds.get("region", "us-east-1")
    if creds.get("access_key_id"):
        try:
            client = boto3.client(
                "ec2",
                aws_access_key_id=creds["access_key_id"],
                aws_secret_access_key=creds["secret_access_key"],
                aws_session_token=creds.get("session_token"),
                region_name=region,
            )
            # Probe to detect expired creds before returning
            client.describe_availability_zones()
            return client
        except Exception as e:
            _auth_err_markers = ("RequestExpired", "AuthFailure", "ExpiredToken",
                                 "InvalidClientTokenId", "validate", "credentials")
            if not any(m in str(e) for m in _auth_err_markers):
                raise
            logger.info("Stored AWS creds invalid/expired; falling back to instance profile")
    return boto3.client("ec2", region_name=region)


async def _take_snapshot(asset_id: str, instance_id: str, connector, organization_id=None) -> dict:
    """Take pre-upgrade EBS snapshot. Returns snapshot metadata dict.

    Stops the instance before snapshotting to guarantee a consistent root
    volume image — crash-consistent snapshots of running instances can capture
    partially-written binaries (ELF corruption) that segfault on restore.
    The instance is restarted immediately after the snapshot is initiated.
    """
    creds = await _get_aws_creds(connector, organization_id)
    region = creds.get("region", "us-east-1")
    ec2 = _make_ec2_client(creds)
    loop = asyncio.get_event_loop()

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

    # Stop instance for consistent snapshot, then restart immediately after.
    logger.info(f"Stopping {instance_id} for consistent pre-upgrade snapshot")
    await loop.run_in_executor(None, lambda: ec2.stop_instances(InstanceIds=[instance_id]))
    await loop.run_in_executor(
        None,
        lambda: ec2.get_waiter("instance_stopped").wait(
            InstanceIds=[instance_id],
            WaiterConfig={"Delay": 10, "MaxAttempts": 30},
        ),
    )

    snap = ec2.create_snapshot(
        VolumeId=root_vol_id,
        Description=f"Pre-OS-upgrade snapshot for {asset_id} at {datetime.now(timezone.utc).isoformat()}",
        TagSpecifications=[{"ResourceType": "snapshot", "Tags": [
            {"Key": "nexplane-purpose", "Value": "pre-os-upgrade"},
            {"Key": "nexplane-asset-id", "Value": asset_id},
        ]}],
    )
    logger.info(f"Snapshot {snap['SnapshotId']} initiated — restarting {instance_id}")
    await loop.run_in_executor(None, lambda: ec2.start_instances(InstanceIds=[instance_id]))
    await loop.run_in_executor(
        None,
        lambda: ec2.get_waiter("instance_running").wait(
            InstanceIds=[instance_id],
            WaiterConfig={"Delay": 10, "MaxAttempts": 30},
        ),
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
    organization_id=None,
) -> dict:
    """Automated EBS root volume swap: stop → create-from-snap → detach → attach → start → verify agent."""
    instance_id = snapshot_meta.get("instance_id")
    root_volume_id = snapshot_meta.get("root_volume_id")
    root_device_name = snapshot_meta.get("root_device_name", "/dev/xvda")
    az = snapshot_meta.get("availability_zone")
    region = snapshot_meta.get("region", "us-east-1")

    if not instance_id or not root_volume_id or not az:
        raise RuntimeError(f"Incomplete snapshot_meta for restore: {snapshot_meta}")

    creds = await _get_aws_creds(connector, organization_id)
    ec2 = _make_ec2_client({**creds, "region": region})
    loop = asyncio.get_event_loop()

    # 0. Wait for snapshot to complete (must happen before create_volume)
    logger.info(f"Waiting for snapshot {snapshot_id} to complete")
    await loop.run_in_executor(
        None,
        lambda: ec2.get_waiter("snapshot_completed").wait(
            SnapshotIds=[snapshot_id],
            WaiterConfig={"Delay": 15, "MaxAttempts": 60},
        ),
    )
    logger.info(f"Snapshot {snapshot_id} ready")

    # 1. Stop instance
    logger.info(f"Stopping instance {instance_id} for EBS restore")
    await loop.run_in_executor(None, lambda: ec2.stop_instances(InstanceIds=[instance_id]))
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
    new_vol = await loop.run_in_executor(
        None,
        lambda: ec2.create_volume(
            SnapshotId=snapshot_id,
            AvailabilityZone=az,
            VolumeType="gp3",
            TagSpecifications=[{"ResourceType": "volume", "Tags": [
                {"Key": "nexplane-purpose", "Value": "os-upgrade-restore"},
                {"Key": "nexplane-asset-id", "Value": asset_id},
            ]}],
        ),
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
        await loop.run_in_executor(
            None,
            lambda: ec2.detach_volume(VolumeId=root_volume_id, InstanceId=instance_id, Force=False),
        )
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
        await loop.run_in_executor(
            None,
            lambda: ec2.create_tags(
                Resources=[root_volume_id],
                Tags=[
                    {"Key": "nexplane-rollback-orphan", "Value": "true"},
                    {"Key": "nexplane-asset-id", "Value": asset_id},
                ],
            ),
        )
    except Exception:
        pass

    # 4. Attach new volume as root
    logger.info(f"Attaching {new_vol_id} as {root_device_name} on {instance_id}")
    await loop.run_in_executor(
        None,
        lambda: ec2.attach_volume(VolumeId=new_vol_id, InstanceId=instance_id, Device=root_device_name),
    )
    await loop.run_in_executor(
        None,
        lambda: ec2.get_waiter("volume_in_use").wait(
            VolumeIds=[new_vol_id],
            WaiterConfig={"Delay": 5, "MaxAttempts": 24},
        ),
    )

    # 5. Start instance
    logger.info(f"Starting instance {instance_id}")
    await loop.run_in_executor(None, lambda: ec2.start_instances(InstanceIds=[instance_id]))
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
        await loop.run_in_executor(
            None,
            lambda: ec2.create_tags(
                Resources=[snapshot_id],
                Tags=[{"Key": "nexplane-rollback-used", "Value": "true"}],
            ),
        )
    except Exception:
        pass

    return {
        "restored": True,
        "new_volume_id": new_vol_id,
        "old_volume_id": root_volume_id,
        "instance_id": instance_id,
        "agent_recovered": agent_recovered,
    }
