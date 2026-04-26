async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "restore_dns_record", "status": "stub"}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "restore_dns_record_rollback", "status": "stub"}
