import asyncio
import random
import string
from datetime import datetime, timezone


def _fake_id(prefix=""): return prefix + "".join(random.choices(string.ascii_lowercase + string.digits, k=12))


def _mock_response(parameters, asset_ids):
    snapshots = [{"asset_id": a, "snapshot_id": _fake_id("snap-"), "size_gb": random.randint(20, 500), "status": "completed"} for a in asset_ids]
    return {"action": "create_snapshot", "snapshot_tag": parameters.get("snapshot_tag", "nexplane-managed"), "snapshots": snapshots, "completed_at": datetime.now(timezone.utc).isoformat()}


async def _real_execute(parameters: dict, asset_ids: list, creds: dict) -> dict:
    from ._client import get_ec2_client
    ec2 = get_ec2_client(creds)
    loop = asyncio.get_event_loop()
    tag = parameters.get("snapshot_tag", "nexplane-managed")
    snapshots = []
    for asset_id in asset_ids:
        volume_id = parameters.get("volume_id") or asset_id
        resp = await loop.run_in_executor(None, lambda vid=volume_id, t=tag: ec2.create_snapshot(
            VolumeId=vid,
            Description=f"Nexplane snapshot - {t}",
            TagSpecifications=[{"ResourceType": "snapshot", "Tags": [{"Key": "nexplane", "Value": t}]}],
        ))
        snapshots.append({
            "asset_id": asset_id,
            "snapshot_id": resp.get("SnapshotId", _fake_id("snap-")),
            "size_gb": resp.get("VolumeSize", 0),
            "status": resp.get("State", "pending"),
        })
    return {"action": "create_snapshot", "snapshot_tag": tag, "snapshots": snapshots, "completed_at": datetime.now(timezone.utc).isoformat()}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return _mock_response(parameters, asset_ids)
    return await _real_execute(parameters, asset_ids, creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "snapshot is its own rollback mechanism"}
