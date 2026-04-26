from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {
        "action": "wait_dns_propagation",
        "ttl_seconds": parameters.get("ttl", 300),
        "propagated": True,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "propagation wait has no rollback"}
