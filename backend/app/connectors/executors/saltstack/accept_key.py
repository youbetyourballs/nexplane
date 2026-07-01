# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    minion_id = parameters["minion_id"]
    if not creds:
        return {"action": "accept_key", "minion_id": minion_id, "accepted": True}
    from ._client import get_token, get_client
    token = await get_token(creds)
    async with get_client(creds, token) as client:
        resp = await client.post("/keys", json={"id": minion_id, "include_accepted": True})
        resp.raise_for_status()
    return {"action": "accept_key", "minion_id": minion_id, "accepted": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.saltstack.reject_key import execute as reject
    return await reject(parameters, [], connector)
