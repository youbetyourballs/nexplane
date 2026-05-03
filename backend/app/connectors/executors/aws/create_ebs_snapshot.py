import asyncio
from datetime import datetime, timezone


def _get_ec2_client(creds: dict):
    from ._client import get_boto3_client
    return get_boto3_client(creds, "ec2")


async def _real_execute(creds: dict, parameters: dict) -> dict:
    ec2 = _get_ec2_client(creds)
    loop = asyncio.get_event_loop()
    volume_id = parameters["volume_id"]
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
    """Rollback deletes the snapshot created during execute."""
    creds = getattr(connector, "credentials", {})
    snapshot_id = execution_result.get("snapshot_id")
    if not snapshot_id or not creds:
        return {"rolled_back": False, "reason": "no snapshot_id in result or no credentials"}
    ec2 = _get_ec2_client(creds)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: ec2.delete_snapshot(SnapshotId=snapshot_id))
    return {"rolled_back": True, "deleted_snapshot_id": snapshot_id}
