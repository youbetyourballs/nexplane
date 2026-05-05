import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    snapshot_name = parameters["snapshot_name"]

    if not creds:
        return {"action": "delete_disk_snapshot", "snapshot_name": snapshot_name, "deleted": True, "mock": True}

    from ._client import get_credentials, get_project_id
    from google.cloud import compute_v1

    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_running_loop()
    client = compute_v1.SnapshotsClient(credentials=credentials)

    op = await loop.run_in_executor(
        None, lambda: client.delete(project=project, snapshot=snapshot_name)
    )
    return {
        "action": "delete_disk_snapshot",
        "snapshot_name": snapshot_name,
        "deleted": True,
        "operation": op.name,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "delete_disk_snapshot is terminal"}
