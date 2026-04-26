async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "schedule_revoke", "status": "stub"}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "schedule_revoke_rollback", "status": "stub"}
