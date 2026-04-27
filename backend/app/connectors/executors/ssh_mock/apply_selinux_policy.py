from datetime import datetime, timezone
import random, string

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    policy_id = "pol-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return {"action": "apply_selinux_policy", "policy_name": parameters.get("policy_name"), "policy_id": policy_id, "assets": asset_ids, "applied": True, "applied_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "revert_selinux_policy", "previous_policy_id": execution_result.get("policy_id")}
