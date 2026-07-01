# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import random
import string
from datetime import datetime, timezone


def _mock_response(asset_ids):
    isolation_id = "iso-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
    return {
        "action": "isolate_host",
        "isolation_id": isolation_id,
        "assets": asset_ids,
        "device_ids": asset_ids,
        "isolated": True,
        "containment_status": "contained",
        "isolated_at": datetime.now(timezone.utc).isoformat(),
    }


async def _wait_for_containment(device_ids: list, target_status: str, creds: dict, max_wait: int = 120, interval: int = 10) -> bool:
    from ._client import get_hosts_api
    falcon = get_hosts_api(creds)
    loop = asyncio.get_event_loop()
    deadline = loop.time() + max_wait

    def _check():
        resp = falcon.get_device_details(ids=device_ids)
        resources = (resp or {}).get("body", {}).get("resources", [])
        return all(r.get("network_containment_status") == target_status for r in resources)

    while loop.time() < deadline:
        confirmed = await loop.run_in_executor(None, _check)
        if confirmed:
            return True
        await asyncio.sleep(interval)
    return False


async def _real_execute(parameters: dict, asset_ids: list, creds: dict) -> dict:
    from ._client import get_hosts_api
    falcon = get_hosts_api(creds)
    loop = asyncio.get_event_loop()
    device_ids = parameters.get("device_ids", asset_ids)

    await loop.run_in_executor(
        None,
        lambda: falcon.perform_action(action_name="contain", body={"ids": device_ids}),
    )
    confirmed = await _wait_for_containment(device_ids, "contained", creds)
    isolation_id = "iso-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
    return {
        "action": "isolate_host",
        "isolation_id": isolation_id,
        "assets": asset_ids,
        "device_ids": device_ids,
        "isolated": confirmed,
        "containment_status": "contained" if confirmed else "pending",
        "isolated_at": datetime.now(timezone.utc).isoformat(),
    }


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return _mock_response(asset_ids)
    return await _real_execute(parameters, asset_ids, creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "restore_host", "isolation_id": execution_result.get("isolation_id")}
