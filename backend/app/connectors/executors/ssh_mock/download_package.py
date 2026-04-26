from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "download_package", "agent_type": parameters.get("agent_type"), "agent_version": parameters.get("agent_version", "8.12.0"), "downloaded": True, "downloaded_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "download has no rollback"}
