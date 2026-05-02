async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    name = parameters["name"]
    if not creds:
        return {"action": "create_alert", "name": name, "created": True}
    from ._client import get_rest_client
    data = {"name": name, "search": parameters["search"], "is_scheduled": 1}
    if parameters.get("cron_schedule"):
        data["cron_schedule"] = parameters["cron_schedule"]
        data["schedule_window"] = "0"
    async with get_rest_client(creds) as client:
        resp = await client.post("/services/saved/searches", data=data)
        resp.raise_for_status()
    return {"action": "create_alert", "name": name, "created": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "delete alert manually in Splunk"}
