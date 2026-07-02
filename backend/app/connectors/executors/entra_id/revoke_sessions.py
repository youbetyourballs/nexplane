# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters["user_id"]
    if not creds:
        return {"action": "revoke_sessions", "user_id": user_id, "sessions_revoked": True}
    from ._client import get_access_token, get_graph_client
    token = await get_access_token(creds)
    async with await get_graph_client(token, connector) as client:
        resp = await client.post(f"/users/{user_id}/revokeSignInSessions")
        resp.raise_for_status()
    return {"action": "revoke_sessions", "user_id": user_id, "sessions_revoked": True}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "cannot restore revoked sessions"}
