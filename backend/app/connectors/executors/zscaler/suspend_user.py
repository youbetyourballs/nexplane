# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters["user_id"]
    if not creds:
        return {"action": "suspend_user", "user_id": user_id, "suspended": True}
    from ._client import get_session
    async with await get_session(creds, connector) as client:
        resp = await client.patch(f"/users/{user_id}", json={"disabled": True})
        resp.raise_for_status()
    return {"action": "suspend_user", "user_id": user_id, "suspended": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.zscaler.activate_user import execute as activate
    return await activate(parameters, [], connector)
