async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "validate_security_rules", "status": "stub"}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "validate_security_rules_rollback", "status": "stub"}
