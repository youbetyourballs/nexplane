from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {
        "action": "apply_windows_patches",
        "packages_updated": 0,
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete",
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "patch application cannot be automatically reversed"}
