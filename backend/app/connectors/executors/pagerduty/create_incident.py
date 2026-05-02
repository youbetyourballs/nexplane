async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "create_incident", "incident_id": "Q0001MOCK", "title": parameters.get("title"), "status": "triggered"}
    from ._client import get_client
    payload = {"incident": {"type": "incident", "title": parameters["title"], "service": {"id": parameters["service_id"], "type": "service_reference"}, "urgency": parameters.get("urgency", "high")}}
    if parameters.get("body"):
        payload["incident"]["body"] = {"type": "incident_body", "details": parameters["body"]}
    async with get_client(creds) as client:
        resp = await client.post("/incidents", json=payload)
        resp.raise_for_status()
        inc = resp.json()["incident"]
    return {"action": "create_incident", "incident_id": inc["id"], "title": inc["title"], "status": inc["status"]}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    incident_id = execution_result.get("incident_id")
    if not incident_id:
        return {"rolled_back": False, "reason": "no incident_id in result"}
    from app.connectors.executors.pagerduty.resolve_incident import execute as resolve
    return await resolve({"incident_id": incident_id}, [], connector)
