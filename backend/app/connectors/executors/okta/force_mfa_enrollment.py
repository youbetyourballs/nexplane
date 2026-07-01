# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    user_id = parameters['user_id']
    if not creds:
        return {"action": "force_mfa_enrollment", "user_id": user_id, "mock": True}
    # Okta enforces MFA enrollment via sign-on policy assignment.
    # This implementation resets factors which forces re-enrollment on next login.
    import httpx
    from ._client import okta_headers, okta_base
    base = okta_base(creds)
    headers = okta_headers(creds)
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{base}/users/{user_id}/lifecycle/reset_factors", headers=headers)
        resp.raise_for_status()
    return {"action": "force_mfa_enrollment", "user_id": user_id, "executed_at": datetime.now(timezone.utc).isoformat()}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "MFA enforcement has no rollback"}
