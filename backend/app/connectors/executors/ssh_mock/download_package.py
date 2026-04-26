async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "download_package", "status": "stub"}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "download_package_rollback", "status": "stub"}
