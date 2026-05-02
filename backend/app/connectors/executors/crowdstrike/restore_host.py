import asyncio
from datetime import datetime, timezone


async def _real_execute(parameters: dict, asset_ids: list, creds: dict) -> dict:
    from ._client import get_hosts_api
    falcon = get_hosts_api(creds)
    loop = asyncio.get_event_loop()
    device_ids = parameters.get("device_ids", asset_ids)
    await loop.run_in_executor(
        None,
        lambda: falcon.perform_action(action_name="lift_containment", body={"ids": device_ids})
    )
    return {"action": "restore_host", "assets": asset_ids, "device_ids": device_ids, "isolated": False, "restored_at": datetime.now(timezone.utc).isoformat()}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "restore_host", "assets": asset_ids, "isolated": False, "restored_at": datetime.now(timezone.utc).isoformat()}
    return await _real_execute(parameters, asset_ids, creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "restore_host has no rollback"}
