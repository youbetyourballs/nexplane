import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    instance_id = parameters.get("instance_id", "")

    if not creds:
        return {"action": "start_instance", "instance_id": instance_id, "lifecycle_state": "RUNNING", "mock": True}

    from ._client import get_compute_client

    compute = get_compute_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: compute.instance_action(instance_id, "START"))

    from .wait_instance_state import execute as wait
    await wait({"instance_id": instance_id, "target_state": "RUNNING"}, [], connector)

    return {"action": "start_instance", "instance_id": instance_id, "lifecycle_state": "RUNNING", "executed_at": datetime.now(timezone.utc).isoformat()}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from .stop_instance import execute as stop
    return await stop({"instance_id": execution_result.get("instance_id") or parameters.get("instance_id", "")}, [], connector)
