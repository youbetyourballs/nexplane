async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "validate_dns_target", "status": "stub"}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "validate_dns_target_rollback", "status": "stub"}
