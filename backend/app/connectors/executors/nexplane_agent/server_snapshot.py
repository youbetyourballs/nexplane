# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""server_snapshot executor -- EC2 AMI creation and rollback."""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

from app.connectors.executors.nexplane_agent.server_backup import _ec2_client, _load_aws_creds

ROLLBACK_CAPABILITY = "full"


async def _do_snapshot(creds: dict, instance_id: str, no_reboot: bool = True) -> dict:
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    def _sync_snapshot():
        ec2 = _ec2_client(creds)
        name = f"nexplane-snapshot-{instance_id}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
        resp = ec2.create_image(
            InstanceId=instance_id,
            Name=name,
            NoReboot=no_reboot,
            TagSpecifications=[{
                "ResourceType": "image",
                "Tags": [
                    {"Key": "nexplane:change_type", "Value": "server_snapshot"},
                    {"Key": "nexplane:instance_id", "Value": instance_id},
                ],
            }],
        )
        ami_id = resp["ImageId"]

        waiter = ec2.get_waiter("image_available")
        waiter.wait(ImageIds=[ami_id])

        image_info = ec2.describe_images(ImageIds=[ami_id])["Images"][0]
        snapshot_ids = [
            bdm["Ebs"]["SnapshotId"]
            for bdm in image_info.get("BlockDeviceMappings", [])
            if "Ebs" in bdm
        ]
        return {
            "status": "completed",
            "artifact_refs": {
                "ami_id": ami_id,
                "snapshot_ids": snapshot_ids,
                "captured_at": datetime.now(timezone.utc).isoformat(),
            },
        }

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor() as pool:
        return await loop.run_in_executor(pool, _sync_snapshot)


async def _deregister_ami(creds: dict, ami_id: str, snapshot_ids: list) -> dict:
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    def _sync_deregister():
        ec2 = _ec2_client(creds)
        ec2.deregister_image(ImageId=ami_id)
        deleted = 0
        for snap_id in snapshot_ids:
            try:
                ec2.delete_snapshot(SnapshotId=snap_id)
                deleted += 1
            except Exception as exc:
                logger.warning("Could not delete snapshot %s: %s", snap_id, exc)
        return {"rolled_back": True, "ami_id": ami_id, "snapshots_deleted": deleted}

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor() as pool:
        return await loop.run_in_executor(pool, _sync_deregister)


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise RuntimeError("server_snapshot: no asset_ids provided")

    aws_connector_id = parameters.get("aws_connector_id", "")
    instance_id = parameters.get("instance_id", "")
    no_reboot = bool(parameters.get("no_reboot", True))

    creds = await _load_aws_creds(aws_connector_id, connector)
    result = await _do_snapshot(creds, instance_id, no_reboot)
    result["_asset_ids"] = [str(a) for a in asset_ids]
    result["_aws_connector_id"] = aws_connector_id
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    artifact_refs = execution_result.get("artifact_refs", {})
    ami_id = artifact_refs.get("ami_id", "")
    snapshot_ids = artifact_refs.get("snapshot_ids", [])

    if not ami_id:
        return {"rolled_back": False, "reason": "no ami_id in execution_result"}

    aws_connector_id = (
        parameters.get("aws_connector_id")
        or execution_result.get("_aws_connector_id")
        or ""
    )
    creds = await _load_aws_creds(aws_connector_id, connector)
    return await _deregister_ami(creds, ami_id, snapshot_ids)
