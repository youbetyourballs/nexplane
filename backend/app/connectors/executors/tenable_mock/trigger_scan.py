from datetime import datetime, timezone
import random, string

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    scan_id = "scan-" + "".join(random.choices(string.digits, k=8))
    return {"action": "trigger_scan", "scan_id": scan_id, "scan_policy": parameters.get("scan_policy", "basic_network_scan"), "assets": asset_ids, "status": "launched", "launched_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "scan trigger has no rollback"}
