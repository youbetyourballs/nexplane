from datetime import datetime, timezone
import random, string

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    isolation_id = "iso-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
    return {"action": "isolate_host", "isolation_id": isolation_id, "assets": asset_ids, "isolated": True, "isolated_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "restore_host", "isolation_id": execution_result.get("isolation_id")}
