from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "restore_bare_metal_service", "process_name": parameters.get("process_name"), "assets": asset_ids, "restored": True, "restored_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "restore_bare_metal_service has no rollback"}
