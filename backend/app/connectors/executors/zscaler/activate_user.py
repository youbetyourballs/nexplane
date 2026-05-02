async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters["user_id"]
    if not creds:
        return {"action": "activate_user", "user_id": user_id, "suspended": False}
    from ._client import get_session
    async with await get_session(creds) as client:
        resp = await client.patch(f"/users/{user_id}", json={"disabled": False})
        resp.raise_for_status()
    return {"action": "activate_user", "user_id": user_id, "suspended": False}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "activate rollback would suspend — use suspend_user explicitly"}
