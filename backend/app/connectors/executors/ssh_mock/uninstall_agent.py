async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "uninstall_agent", "status": "stub"}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "uninstall_agent_rollback", "status": "stub"}
