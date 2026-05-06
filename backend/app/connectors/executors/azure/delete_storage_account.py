import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    name = parameters.get("storage_account_name", "")
    rg = parameters.get("resource_group", creds.get("resource_group", "default"))

    if not creds:
        return {"action": "delete_storage_account", "storage_account_name": name, "mock": True}

    from ._client import get_storage_client
    storage = get_storage_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: storage.storage_accounts.delete(rg, name))
    return {
        "action": "delete_storage_account",
        "storage_account_name": name,
        "resource_group": rg,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "storage account deletion cannot be reversed automatically"}
