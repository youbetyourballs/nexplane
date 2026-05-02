from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "revert_selinux_policy", "previous_policy_id": parameters.get("previous_policy_id"), "assets": asset_ids, "reverted": True, "reverted_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "revert_selinux_policy has no rollback"}
