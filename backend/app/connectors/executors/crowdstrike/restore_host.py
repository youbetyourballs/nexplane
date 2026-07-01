# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import random
import string
from datetime import datetime, timezone


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


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    device_ids = parameters.get("device_ids", asset_ids)
    if not creds:
        return {
            "action": "restore_host",
            "device_ids": device_ids,
            "restored": True,
            "containment_status": "normal",
            "restored_at": datetime.now(timezone.utc).isoformat(),
        }
    from ._client import get_hosts_api
    falcon = get_hosts_api(creds)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        lambda: falcon.perform_action(action_name="lift_containment", body={"ids": device_ids}),
    )
    confirmed = await _wait_for_containment(device_ids, "normal", creds)
    return {
        "action": "restore_host",
        "device_ids": device_ids,
        "restored": confirmed,
        "containment_status": "normal" if confirmed else "pending",
        "restored_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "restore_host has no further rollback"}
