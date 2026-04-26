async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "analyze_flows", "status": "stub"}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "analyze_flows_rollback", "status": "stub"}
