async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "generate_diff", "status": "stub"}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "generate_diff_rollback", "status": "stub"}
