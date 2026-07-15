# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    user_id = parameters['user_id']
    if not creds:
        return {"action": "reset_mfa_factors", "user_id": user_id, "mock": True}
    from ._client import okta_headers, okta_base, get_client
    base = okta_base(creds)
    headers = okta_headers(creds)
    async with await get_client(connector) as client:
        # List and delete all enrolled factors
        resp = await client.get(f"{base}/users/{user_id}/factors", headers=headers)
        resp.raise_for_status()
        factor_ids = [f['id'] for f in resp.json()]
        for fid in factor_ids:
            del_resp = await client.delete(f"{base}/users/{user_id}/factors/{fid}", headers=headers)
            del_resp.raise_for_status()
    return {"action": "reset_mfa_factors", "user_id": user_id, "factors_removed": len(factor_ids), "executed_at": datetime.now(timezone.utc).isoformat()}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "MFA factor reset has no rollback — user must re-enroll"}
