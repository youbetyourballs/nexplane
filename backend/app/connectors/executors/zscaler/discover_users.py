async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_users", "users": [{"id": 1, "name": "Mock User", "email": "user@example.com"}], "count": 1}
    from ._client import get_session
    async with await get_session(creds) as client:
        resp = await client.get("/users", params={"pageSize": 1000})
        resp.raise_for_status()
        users = [{"id": u.get("id"), "name": u.get("name"), "email": u.get("email"), "department": u.get("department", {}).get("name")} for u in resp.json()]
    return {"action": "discover_users", "users": users, "count": len(users)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
