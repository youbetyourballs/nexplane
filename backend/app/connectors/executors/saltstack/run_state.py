async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    target = parameters.get("target", "*")
    state = parameters.get("state")
    if not creds:
        return {"action": "run_state", "target": target, "state": state, "job_id": "mock-jid-001"}
    from ._client import get_token, get_client
    token = await get_token(creds)
    async with get_client(creds, token) as client:
        resp = await client.post("/", json=[{"client": "local_async", "tgt": target, "fun": "state.apply", "arg": [state] if state else []}])
        resp.raise_for_status()
        result = resp.json().get("return", [{}])[0]
    return {"action": "run_state", "target": target, "state": state, "job_id": result.get("jid")}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "state runs cannot be automatically reversed"}
