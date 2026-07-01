# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    minion_id = parameters["minion_id"]
    if not creds:
        return {"action": "reject_key", "minion_id": minion_id, "rejected": True}
    from ._client import get_token, get_client
    token = await get_token(creds)
    async with get_client(creds, token) as client:
        resp = await client.delete(f"/keys/{minion_id}")
        resp.raise_for_status()
    return {"action": "reject_key", "minion_id": minion_id, "rejected": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "rejected key cannot be automatically re-accepted"}
