# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    user_id = parameters['user_id']
    if not creds:
        return {"action": "reset_password", "user_id": user_id, "mock": True}
    from ._client import okta_headers, okta_base, get_client
    async with await get_client(connector) as client:
        resp = await client.post(f"{okta_base(creds)}/users/{user_id}/lifecycle/reset_password?sendEmail=true", headers=okta_headers(creds))
        resp.raise_for_status()
    return {"action": "reset_password", "user_id": user_id, "executed_at": datetime.now(timezone.utc).isoformat()}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "password reset has no rollback"}
