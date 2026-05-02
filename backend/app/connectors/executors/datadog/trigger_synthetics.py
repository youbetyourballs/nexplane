async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    test_id = parameters["test_public_id"]
    if not creds:
        return {"action": "trigger_synthetics", "test_id": test_id, "triggered": True}
    from ._client import get_v1_client
    async with get_v1_client(creds) as client:
        resp = await client.post("/synthetics/tests/trigger/ci", json={"tests": [{"public_id": test_id}]})
        resp.raise_for_status()
    return {"action": "trigger_synthetics", "test_id": test_id, "triggered": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "synthetic test trigger has no rollback"}
