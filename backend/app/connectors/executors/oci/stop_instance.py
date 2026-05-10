import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    instance_id = parameters.get("instance_id", "")

    if not creds:
        return {"action": "stop_instance", "instance_id": instance_id, "lifecycle_state": "STOPPED", "mock": True}

    from ._client import get_compute_client

    compute = get_compute_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: compute.instance_action(instance_id, "STOP"))

    from .wait_instance_state import execute as wait
    await wait({"instance_id": instance_id, "target_state": "STOPPED"}, [], connector)

    return {"action": "stop_instance", "instance_id": instance_id, "lifecycle_state": "STOPPED", "executed_at": datetime.now(timezone.utc).isoformat()}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from .start_instance import execute as start
    return await start({"instance_id": execution_result.get("instance_id") or parameters.get("instance_id", "")}, [], connector)
