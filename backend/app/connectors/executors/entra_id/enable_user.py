# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters["user_id"]
    if not creds:
        return {"action": "enable_user", "user_id": user_id, "account_enabled": True}
    from ._client import get_access_token, get_graph_client
    token = await get_access_token(creds)
    async with get_graph_client(token) as client:
        resp = await client.patch(f"/users/{user_id}", json={"accountEnabled": True})
        resp.raise_for_status()
    return {"action": "enable_user", "user_id": user_id, "account_enabled": True}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "enable rollback would disable — use disable_user explicitly"}
