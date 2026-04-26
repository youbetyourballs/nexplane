async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "install_agent", "status": "stub"}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "install_agent_rollback", "status": "stub"}
