import random, string
from datetime import datetime, timezone

def _fake_id(prefix=""): return prefix + "".join(random.choices(string.ascii_lowercase + string.digits, k=12))

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "generate_key", "new_key_id": _fake_id("key-"), "old_key_id": _fake_id("key-old-"), "key_type": parameters.get("key_type", "api_key"), "service": parameters.get("service"), "generated_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "key generation has no rollback"}
