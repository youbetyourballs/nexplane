async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "check_prerequisites", "status": "stub"}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "check_prerequisites_rollback", "status": "stub"}
