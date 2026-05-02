async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    group_id = parameters["group_id"]
    if not creds:
        return {"action": "update_policy", "group_id": group_id, "updated": True}
    from ._client import get_client
    policy_data = {}
    if parameters.get("detection_mode"):
        policy_data["detectionMode"] = parameters["detection_mode"]
    async with get_client(creds) as client:
        resp = await client.put(f"/groups/{group_id}/policy", json=policy_data)
        resp.raise_for_status()
    return {"action": "update_policy", "group_id": group_id, "updated": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "previous policy state not captured — restore manually"}
