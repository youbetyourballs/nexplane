# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    user_id = parameters['user_id']
    if not creds:
        return {"action": "revoke_sessions", "user_id": user_id, "mock": True}
    from ._client import okta_headers, okta_base, get_client
    async with await get_client(connector) as client:
        resp = await client.delete(f"{okta_base(creds)}/users/{user_id}/sessions", headers=okta_headers(creds))
        resp.raise_for_status()
    return {"action": "revoke_sessions", "user_id": user_id, "executed_at": datetime.now(timezone.utc).isoformat()}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "session revocation has no rollback"}
