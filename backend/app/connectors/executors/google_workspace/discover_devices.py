import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_devices", "devices": [], "count": 0}
    from ._client import get_admin_service
    loop = asyncio.get_event_loop()
    service = get_admin_service(creds, "admin", "directory_v1")
    result = await loop.run_in_executor(None, lambda: service.mobiledevices().list(customerId="my_customer", maxResults=100).execute())
    devices = [{"resource_id": d.get("resourceId"), "email": d.get("email"), "os": d.get("os"), "status": d.get("status"), "last_sync": d.get("lastSync")} for d in result.get("mobiledevices", [])]
    return {"action": "discover_devices", "devices": devices, "count": len(devices)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
