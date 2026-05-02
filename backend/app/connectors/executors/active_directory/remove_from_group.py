from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "remove_from_group", "username": parameters.get("username"), "group_name": parameters.get("group_name"), "removed": True, "removed_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "remove_from_group has no automatic rollback"}
