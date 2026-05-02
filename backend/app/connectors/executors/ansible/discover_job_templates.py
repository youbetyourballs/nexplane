async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_job_templates", "templates": [{"id": 1, "name": "Deploy App", "playbook": "deploy.yml"}], "count": 1}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.get("/job_templates/", params={"page_size": 100})
        resp.raise_for_status()
        templates = [{"id": t["id"], "name": t["name"], "playbook": t.get("playbook")} for t in resp.json().get("results", [])]
    return {"action": "discover_job_templates", "templates": templates, "count": len(templates)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
