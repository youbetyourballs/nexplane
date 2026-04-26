async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "create_snapshot", "status": "stub"}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "create_snapshot_rollback", "status": "stub"}
