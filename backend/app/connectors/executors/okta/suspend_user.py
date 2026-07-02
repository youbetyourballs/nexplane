# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone


async def _wait_for_status(user_id: str, target_status: str, connector, max_wait: int = 30, interval: int = 3) -> None:
    from ._client import okta_headers, okta_base, get_client
    creds = getattr(connector, "credentials", {}) or {}
    deadline = asyncio.get_event_loop().time() + max_wait
    while asyncio.get_event_loop().time() < deadline:
        async with await get_client(connector) as client:
            resp = await client.get(f"{okta_base(creds)}/users/{user_id}", headers=okta_headers(creds))
            if resp.status_code == 200 and resp.json().get("status") == target_status:
                return
        await asyncio.sleep(interval)
    raise TimeoutError(f"Okta user {user_id} did not reach status '{target_status}' within {max_wait}s")


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    user_id = parameters['user_id']
    if not creds:
        return {"action": "suspend_user", "user_id": user_id, "status": "SUSPENDED", "mock": True}
    from ._client import okta_headers, okta_base, get_client
    async with await get_client(connector) as client:
        resp = await client.post(f"{okta_base(creds)}/users/{user_id}/lifecycle/suspend", headers=okta_headers(creds))
        resp.raise_for_status()
    await _wait_for_status(user_id, "SUSPENDED", connector, max_wait=30, interval=3)
    return {"action": "suspend_user", "user_id": user_id, "status": "SUSPENDED", "executed_at": datetime.now(timezone.utc).isoformat()}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "use unsuspend_user to roll back"}
