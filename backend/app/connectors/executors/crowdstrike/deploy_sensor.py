from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "deploy_sensor", "sensor_version": parameters.get("sensor_version", "7.14.0"), "assets": asset_ids, "status": "installed", "deployed_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "remove_sensor", "assets": execution_result.get("assets", [])}
