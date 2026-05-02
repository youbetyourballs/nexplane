async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "create_incident", "number": "INC-MOCK-001", "sys_id": "mock-sys-id"}
    from ._client import get_client
    body = {"short_description": parameters["short_description"]}
    if parameters.get("description"):
        body["description"] = parameters["description"]
    if parameters.get("urgency"):
        body["urgency"] = str(parameters["urgency"])
    if parameters.get("impact"):
        body["impact"] = str(parameters["impact"])
    if parameters.get("category"):
        body["category"] = parameters["category"]
    async with get_client(creds) as client:
        resp = await client.post("/incident", json=body)
        resp.raise_for_status()
        inc = resp.json()["result"]
    return {"action": "create_incident", "number": inc.get("number"), "sys_id": inc.get("sys_id")}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "close incident manually"}
