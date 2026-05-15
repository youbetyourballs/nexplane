from __future__ import annotations

from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters["user_id"]
    if not creds:
        return {"action": "disable_user", "user_id": user_id, "previous_status": "ACTIVE", "status": "DEPROVISIONED", "mock": True}
    import httpx
    from ._client import okta_headers, okta_base
    base = okta_base(creds)
    headers = okta_headers(creds)
    async with httpx.AsyncClient() as client:
        # Capture current status for rollback
        get_resp = await client.get(f"{base}/users/{user_id}", headers=headers)
        get_resp.raise_for_status()
        previous_status = get_resp.json().get("status", "ACTIVE")
        # Deactivate
        resp = await client.post(f"{base}/users/{user_id}/lifecycle/deactivate", headers=headers)
        resp.raise_for_status()
    return {
        "action": "disable_user",
        "user_id": user_id,
        "previous_status": previous_status,
        "status": "DEPROVISIONED",
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = execution_result.get("user_id") or parameters.get("user_id")
    if not user_id:
        return {"rolled_back": False, "reason": "no user_id in execution_result"}
    if not creds:
        return {"rolled_back": True, "user_id": user_id, "mock": True}
    import httpx
    from ._client import okta_headers, okta_base
    base = okta_base(creds)
    headers = okta_headers(creds)
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{base}/users/{user_id}/lifecycle/activate?sendEmail=false", headers=headers)
        resp.raise_for_status()
    return {"rolled_back": True, "user_id": user_id, "action": "activate_user"}
