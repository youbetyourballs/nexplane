async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "start_service", "status": "stub"}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "start_service_rollback", "status": "stub"}
