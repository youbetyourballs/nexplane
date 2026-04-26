from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {
        "action": "capture_dns_record",
        "record_name": parameters.get("record_name"),
        "record_type": parameters.get("record_type", "A"),
        "current_value": "203.0.113.10",
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "capture has no rollback"}
