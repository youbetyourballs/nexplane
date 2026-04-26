from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "start_service", "agent_type": parameters.get("agent_type"), "service_status": "active", "hosts": [{"asset_id": a, "service_status": "active"} for a in asset_ids], "completed_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "service start has no automatic rollback"}
