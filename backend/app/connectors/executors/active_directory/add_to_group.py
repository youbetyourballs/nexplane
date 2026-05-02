from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "add_to_group", "username": parameters.get("username"), "group_name": parameters.get("group_name"), "added": True, "added_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "remove_from_group", "username": parameters.get("username"), "group_name": parameters.get("group_name")}
