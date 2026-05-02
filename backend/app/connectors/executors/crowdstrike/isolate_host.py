import asyncio
import random
import string
from datetime import datetime, timezone


def _mock_response(asset_ids):
    isolation_id = "iso-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
    return {"action": "isolate_host", "isolation_id": isolation_id, "assets": asset_ids, "isolated": True, "isolated_at": datetime.now(timezone.utc).isoformat()}


async def _real_execute(parameters: dict, asset_ids: list, creds: dict) -> dict:
    from ._client import get_hosts_api
    falcon = get_hosts_api(creds)
    loop = asyncio.get_event_loop()
    device_ids = parameters.get("device_ids", asset_ids)
    await loop.run_in_executor(
        None,
        lambda: falcon.perform_action(action_name="contain", body={"ids": device_ids})
    )
    isolation_id = "iso-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
    return {"action": "isolate_host", "isolation_id": isolation_id, "assets": asset_ids, "device_ids": device_ids, "isolated": True, "isolated_at": datetime.now(timezone.utc).isoformat()}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return _mock_response(asset_ids)
    return await _real_execute(parameters, asset_ids, creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "restore_host", "isolation_id": execution_result.get("isolation_id")}
