import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    alarm_id = parameters.get("alarm_id", "")

    if not creds:
        return {"action": "delete_alarm", "alarm_id": alarm_id, "mock": True}

    from ._client import get_monitoring_client
    mon_client = get_monitoring_client(creds)
    loop = asyncio.get_running_loop()

    await loop.run_in_executor(None, lambda: mon_client.delete_alarm(alarm_id=alarm_id))
    return {
        "action": "delete_alarm",
        "alarm_id": alarm_id,
        "status": "DELETED",
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }
