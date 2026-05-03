from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters["user_id"]
    sku_id = parameters["sku_id"]

    if not creds:
        return {"action": "remove_license", "user_id": user_id, "sku_id": sku_id, "removed": True, "mock": True}

    from ._client import get_access_token, get_graph_client
    token = await get_access_token(creds)

    async with get_graph_client(token) as client:
        current_resp = await client.get(f"/users/{user_id}?$select=assignedLicenses")
        current_resp.raise_for_status()
        previous_licenses = current_resp.json().get("assignedLicenses", [])

        remove_resp = await client.post(
            f"/users/{user_id}/assignLicense",
            json={"addLicenses": [], "removeLicenses": [sku_id]},
        )
        remove_resp.raise_for_status()

    return {
        "action": "remove_license",
        "user_id": user_id,
        "sku_id": sku_id,
        "removed": True,
        "rollback_data": {"previous_licenses": previous_licenses},
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.entra_id.assign_license import execute as assign
    return await assign(parameters, [], connector)
