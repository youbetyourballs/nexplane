async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "cancel_revoke", "status": "stub"}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "cancel_revoke_rollback", "status": "stub"}
