from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {
        "action": "restore_dns_record",
        "record_name": parameters.get("record_name"),
        "restored_value": parameters.get("previous_value"),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "restore has no further rollback"}
