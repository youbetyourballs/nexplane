async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "collect_output", "status": "stub"}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "collect_output_rollback", "status": "stub"}
