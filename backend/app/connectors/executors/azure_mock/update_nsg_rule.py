from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "update_nsg_rule", "rule_name": parameters.get("rule_name"), "nsg_action": parameters.get("action"), "priority": parameters.get("priority", 100), "assets": asset_ids, "applied": True, "applied_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "restore_nsg_rule", "rule_name": parameters.get("rule_name")}
