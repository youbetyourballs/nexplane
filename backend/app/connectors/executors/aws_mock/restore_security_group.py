from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "restore_security_group", "restored_from_snapshot": parameters.get("pre_change_snapshot_id"), "completed_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "restore has no further rollback"}
