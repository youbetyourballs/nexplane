async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "wait_dns_propagation", "status": "stub"}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "wait_dns_propagation_rollback", "status": "stub"}
