from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "cancel_revoke", "old_key_id": parameters.get("old_key_id"), "old_key_status": "active", "cancelled_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "cancel has no further rollback"}
