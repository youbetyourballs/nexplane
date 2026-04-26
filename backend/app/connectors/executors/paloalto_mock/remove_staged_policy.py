async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "remove_staged_policy", "status": "stub"}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "remove_staged_policy_rollback", "status": "stub"}
