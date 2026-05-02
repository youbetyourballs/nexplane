from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    user_id = parameters['user_id']
    if not creds:
        return {"action": "deactivate_user", "user_id": user_id, "mock": True}
    import httpx
    from ._client import okta_headers, okta_base
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{okta_base(creds)}/users/{user_id}/lifecycle/deactivate", headers=okta_headers(creds))
        resp.raise_for_status()
    return {"action": "deactivate_user", "user_id": user_id, "executed_at": datetime.now(timezone.utc).isoformat()}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "use reactivate_user to roll back"}
