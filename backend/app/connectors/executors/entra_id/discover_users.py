async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_users", "users": [
            {"id": "mock-user-id", "upn": "alice@example.com", "display_name": "Alice", "account_enabled": True}
        ], "count": 1}
    from ._client import get_access_token, get_graph_client
    token = await get_access_token(creds)
    async with get_graph_client(token) as client:
        resp = await client.get("/users", params={"$select": "id,userPrincipalName,displayName,accountEnabled,lastSignInDateTime", "$top": 999})
        resp.raise_for_status()
        users = [{"id": u["id"], "upn": u.get("userPrincipalName"), "display_name": u.get("displayName"), "account_enabled": u.get("accountEnabled")} for u in resp.json().get("value", [])]
    return {"action": "discover_users", "users": users, "count": len(users)}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
