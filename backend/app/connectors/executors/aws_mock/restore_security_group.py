async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "restore_security_group", "status": "stub"}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "restore_security_group_rollback", "status": "stub"}
