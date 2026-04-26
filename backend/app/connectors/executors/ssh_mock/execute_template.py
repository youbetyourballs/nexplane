async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "execute_template", "status": "stub"}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "execute_template_rollback", "status": "stub"}
