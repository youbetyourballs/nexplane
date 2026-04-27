from datetime import datetime, timezone
import random, string

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    new_key = "".join(random.choices(string.ascii_letters + string.digits, k=64))
    return {"action": "rotate_storage_key", "key_name": parameters.get("key_name", "key1"), "assets": asset_ids, "new_key_fingerprint": new_key[:8] + "...", "rotated_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "key rotation has no rollback — update consumers to new key"}
