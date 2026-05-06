import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    alert_name = parameters.get("alert_name", "")
    rg = parameters.get("resource_group", creds.get("resource_group", "default"))

    if not creds:
        return {"action": "delete_metric_alert", "alert_name": alert_name, "resource_group": rg, "mock": True}

    from ._client import get_monitor_client
    monitor = get_monitor_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: monitor.metric_alerts.delete(rg, alert_name))
    return {
        "action": "delete_metric_alert",
        "alert_name": alert_name,
        "resource_group": rg,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "metric alert deletion cannot be reversed automatically"}
