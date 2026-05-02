from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "restore_chokepoint_rule", "rule_name": parameters.get("rule_name"), "restored": True, "restored_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "restore_chokepoint_rule has no rollback"}
