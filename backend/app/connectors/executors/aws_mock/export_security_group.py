import random, string
from datetime import datetime, timezone

def _fake_id(prefix=""): return prefix + "".join(random.choices(string.ascii_lowercase + string.digits, k=12))

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "export_security_group", "group_id": parameters.get("group_id", _fake_id("sg-")), "snapshot_id": _fake_id("sgsnap-"), "rules_captured": 3, "captured_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "export has no rollback"}
