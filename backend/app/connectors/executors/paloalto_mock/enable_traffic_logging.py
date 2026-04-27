from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "enable_traffic_logging", "zone_name": parameters.get("zone_name"), "rule_name": parameters.get("rule_name"), "logging_enabled": True, "enabled_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "disable_traffic_logging"}
