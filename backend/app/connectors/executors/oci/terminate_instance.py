import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    instance_id = parameters.get("instance_id", "")
    preserve_boot_volume = parameters.get("preserve_boot_volume", False)

    if not creds:
        return {"action": "terminate_instance", "instance_id": instance_id, "lifecycle_state": "TERMINATED", "mock": True}

    from ._client import get_compute_client

    compute = get_compute_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: compute.terminate_instance(instance_id, preserve_boot_volume=preserve_boot_volume))

    from .wait_instance_state import execute as wait
    await wait({"instance_id": instance_id, "target_state": "TERMINATED"}, [], connector)

    return {"action": "terminate_instance", "instance_id": instance_id, "lifecycle_state": "TERMINATED", "executed_at": datetime.now(timezone.utc).isoformat()}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "terminate is destructive — no rollback"}
