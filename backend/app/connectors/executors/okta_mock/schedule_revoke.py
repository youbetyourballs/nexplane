from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "schedule_revoke", "grace_period_hours": parameters.get("grace_period_hours", 24), "revocation_scheduled_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "revocation_cancelled", "old_key_id": execution_result.get("old_key_id"), "old_key_status": "active", "completed_at": datetime.now(timezone.utc).isoformat()}
