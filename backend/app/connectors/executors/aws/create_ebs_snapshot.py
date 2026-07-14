# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone


ROLLBACK_CAPABILITY = "full"


def _get_ec2_client(creds: dict):
    from ._client import get_boto3_client
    return get_boto3_client(creds, "ec2")


async def _real_execute(creds: dict, parameters: dict) -> dict:
    ec2 = _get_ec2_client(creds)
    loop = asyncio.get_event_loop()
    volume_id = parameters.get("volume_id", "")

    # If no volume_id but instance_id provided, look up the root volume
    if not volume_id and parameters.get("instance_id"):
        instance_id = parameters["instance_id"]

        def _lookup_volume():
            resp = ec2.describe_instances(InstanceIds=[instance_id])
            block_devs = resp["Reservations"][0]["Instances"][0].get("BlockDeviceMappings", [])
            for bd in block_devs:
                if bd.get("Ebs", {}).get("VolumeId"):
                    return bd["Ebs"]["VolumeId"]
            return None

        volume_id = await loop.run_in_executor(None, _lookup_volume) or ""

    if not volume_id:
        return {"action": "create_ebs_snapshot", "skipped": True, "reason": "no volume_id available"}

    name = parameters.get("backup_name", "nexplane-backup")
    retention = int(parameters.get("retention_days", 30))

    def _call():
        return ec2.create_snapshot(
            VolumeId=volume_id,
            Description=name,
            TagSpecifications=[{
                "ResourceType": "snapshot",
                "Tags": [
                    {"Key": "Name",          "Value": name},
                    {"Key": "RetentionDays", "Value": str(retention)},
                    {"Key": "ManagedBy",     "Value": "nexplane"},
                ],
            }],
        )

    resp = await loop.run_in_executor(None, _call)
    return {
        "action":      "create_ebs_snapshot",
        "snapshot_id": resp["SnapshotId"],
        "state":       resp["State"],
        "volume_id":   volume_id,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {
            "action":      "create_ebs_snapshot",
            "volume_id":   parameters.get("volume_id"),
            "snapshot_id": "snap-mock-0000",
            "state":       "pending",
            "mock":        True,
        }
    return await _real_execute(creds, parameters)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "note": "rollback handled by paired catalog action: delete_ebs_snapshot"}
