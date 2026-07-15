# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

ROLLBACK_CAPABILITY = "full"

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    token_id = parameters["token_id"]
    if not creds:
        return {"action": "revoke_oauth_token", "token_id": token_id, "revoked": True}
    from ._client import get_client
    org = creds["org"]
    async with await get_client(connector) as client:
        resp = await client.delete(f"/orgs/{org}/credential-authorizations/{token_id}")
        resp.raise_for_status()
    return {"action": "revoke_oauth_token", "token_id": token_id, "revoked": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "revoked tokens cannot be restored"}
