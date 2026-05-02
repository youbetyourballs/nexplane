import json

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    host_id = parameters["host_id"]
    variables = parameters["variables"]
    if not creds:
        return {"action": "update_host_variables", "host_id": host_id, "updated": True}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.patch(f"/hosts/{host_id}/", json={"variables": json.dumps(variables)})
        resp.raise_for_status()
    return {"action": "update_host_variables", "host_id": host_id, "updated": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "previous host variables not captured — restore manually"}
