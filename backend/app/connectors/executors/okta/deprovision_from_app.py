from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    user_id = parameters['user_id']
    app_id = parameters['app_id']
    if not creds:
        return {"action": "deprovision_from_app", "user_id": user_id, "app_id": app_id, "mock": True}
    import httpx
    from ._client import okta_headers, okta_base
    async with httpx.AsyncClient() as client:
        resp = await client.delete(f"{okta_base(creds)}/apps/{app_id}/users/{user_id}", headers=okta_headers(creds))
        resp.raise_for_status()
    return {"action": "deprovision_from_app", "user_id": user_id, "app_id": app_id, "executed_at": datetime.now(timezone.utc).isoformat()}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "re-assign user to app manually to roll back"}
