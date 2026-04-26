async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "health_check", "status": "stub"}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "health_check_rollback", "status": "stub"}
