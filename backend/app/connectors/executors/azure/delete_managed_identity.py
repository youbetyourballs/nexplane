import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    name = parameters.get("identity_name", "")
    rg = parameters.get("resource_group", creds.get("resource_group", "default"))

    if not creds:
        return {"action": "delete_managed_identity", "identity_name": name, "resource_group": rg, "mock": True}

    from ._client import get_msi_client
    msi = get_msi_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: msi.user_assigned_identities.delete(rg, name))
    return {
        "action": "delete_managed_identity",
        "identity_name": name,
        "resource_group": rg,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "managed identity deletion cannot be reversed automatically"}
