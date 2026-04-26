import random, string
from datetime import datetime, timezone

def _fake_id(prefix=""): return prefix + "".join(random.choices(string.ascii_lowercase + string.digits, k=12))

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    rules = parameters.get("rules", [])
    return {"action": "update_security_group", "group_id": parameters.get("group_id", _fake_id("sg-")), "rules_applied": len(rules), "rules_added": [r for r in rules if r.get("action") == "add"], "rules_removed": [r for r in rules if r.get("action") == "remove"], "pre_change_snapshot_id": _fake_id("sgsnap-"), "completed_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "security_group_restore", "restored_from_snapshot": execution_result.get("pre_change_snapshot_id"), "completed_at": datetime.now(timezone.utc).isoformat()}
