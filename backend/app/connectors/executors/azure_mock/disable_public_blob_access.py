from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "disable_public_blob_access", "assets": asset_ids, "public_access": False, "applied_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "enable_public_blob_access", "assets": execution_result.get("assets", [])}
