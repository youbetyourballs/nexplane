async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters["user_id"]
    if not creds:
        return {"action": "disable_user", "user_id": user_id, "account_enabled": False}
    from ._client import get_access_token, get_graph_client
    token = await get_access_token(creds)
    async with get_graph_client(token) as client:
        resp = await client.patch(f"/users/{user_id}", json={"accountEnabled": False})
        resp.raise_for_status()
    return {"action": "disable_user", "user_id": user_id, "account_enabled": False}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.entra_id.enable_user import execute as enable
    return await enable(parameters, [], connector)
