from datetime import datetime, timezone
from .defender_client import get_defender_client


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    machine_id = parameters.get("machine_id", "")
    if not machine_id:
        raise ValueError("machine_id is required")
    comment = parameters.get("comment", "Nexplane automated isolation")
    client = get_defender_client(connector)
    if not client:
        return {"status": "skipped", "reason": "no_defender_credentials", "machine_id": machine_id}
    result = await client.isolate_machine(machine_id, comment)
    return {
        "machine_id": machine_id,
        "action_id": result.get("id"),
        "status": result.get("status", "Pending"),
        "type": result.get("type"),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    machine_id = parameters.get("machine_id", "")
    client = get_defender_client(connector)
    if not client:
        return {"rolled_back": False, "reason": "no_defender_credentials"}
    result = await client.unisolate_machine(machine_id, "Nexplane rollback: remove isolation")
    return {"rolled_back": True, "machine_id": machine_id, "action_id": result.get("id"), "status": result.get("status")}
