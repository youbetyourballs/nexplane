import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    instance_name = parameters["instance_name"]
    zone = parameters["zone"]
    snapshot_name = parameters.get("snapshot_name", f"nexplane-snap-{instance_name}")

    if not creds:
        return {
            "action": "create_disk_snapshot",
            "instance_name": instance_name,
            "zone": zone,
            "snapshot_name": snapshot_name,
            "disk_name": instance_name,
            "mock": True,
        }

    from ._client import get_credentials, get_project_id
    from google.cloud import compute_v1

    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_running_loop()

    # Get boot disk name from instance
    instances_client = compute_v1.InstancesClient(credentials=credentials)
    instance = await loop.run_in_executor(
        None, lambda: instances_client.get(project=project, zone=zone, instance=instance_name)
    )
    disk_name = instance.disks[0].source.split("/")[-1]

    # Create snapshot from disk
    snapshot_body = compute_v1.Snapshot(
        name=snapshot_name,
        labels={"managed-by": "nexplane", "source-instance": instance_name},
    )
    disks_client = compute_v1.DisksClient(credentials=credentials)
    op = await loop.run_in_executor(
        None,
        lambda: disks_client.create_snapshot(
            project=project, zone=zone, disk=disk_name, snapshot_resource=snapshot_body
        ),
    )
    return {
        "action": "create_disk_snapshot",
        "instance_name": instance_name,
        "zone": zone,
        "snapshot_name": snapshot_name,
        "disk_name": disk_name,
        "operation": op.name,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.gcp.delete_disk_snapshot import execute as delete_snap
    return await delete_snap(
        {"snapshot_name": execution_result.get("snapshot_name", parameters.get("snapshot_name"))},
        [], connector,
    )
