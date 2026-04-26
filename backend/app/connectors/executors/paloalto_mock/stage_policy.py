import random, string
from datetime import datetime, timezone

def _fake_id(prefix=""): return prefix + "".join(random.choices(string.ascii_lowercase + string.digits, k=12))

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    rules = parameters.get("policy_rules", [])
    critical = parameters.get("critical_flows", [])
    return {"action": "stage_policy", "mode": "simulation", "staged_policy_id": _fake_id("pol-"), "rules_staged": len(rules), "simulation_result": "no_violations", "critical_flows_checked": len(critical), "note": "Policy staged in simulation mode only.", "completed_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "staged_policy_removed", "policy_id": execution_result.get("staged_policy_id"), "completed_at": datetime.now(timezone.utc).isoformat()}
