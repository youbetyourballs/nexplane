from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "disable_traffic_logging", "zone_name": parameters.get("zone_name"), "rule_name": parameters.get("rule_name"), "logging_enabled": False, "disabled_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "disable_traffic_logging has no rollback"}
