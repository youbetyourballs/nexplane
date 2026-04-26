async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "distribute_key", "status": "stub"}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "distribute_key_rollback", "status": "stub"}
