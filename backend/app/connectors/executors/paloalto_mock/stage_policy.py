async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "stage_policy", "status": "stub"}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "stage_policy_rollback", "status": "stub"}
