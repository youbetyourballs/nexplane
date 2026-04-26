import random
import string
from datetime import datetime, timezone

def _fake_id(prefix: str = "") -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
    return f"{prefix}{suffix}"

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {
        "action": "update_dns_record",
        "record_name": parameters.get("record_name"),
        "record_type": parameters.get("record_type", "A"),
        "previous_value": "203.0.113.10",
        "new_value": parameters.get("new_value"),
        "ttl": parameters.get("ttl", 300),
        "propagation_id": _fake_id("prop-"),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": True,
        "action": "restore_dns_record",
        "restored_value": execution_result.get("previous_value", "unknown"),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
