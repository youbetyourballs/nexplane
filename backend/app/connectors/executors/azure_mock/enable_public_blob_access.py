import asyncio
from datetime import datetime, timezone


async def _real_execute(parameters: dict, asset_ids: list, creds: dict) -> dict:
    from ._client import get_storage_client
    storage = get_storage_client(creds)
    loop = asyncio.get_event_loop()
    rg = parameters.get("resource_group", creds.get("resource_group", "default"))
    for asset_id in asset_ids:
        acct_name = parameters.get("storage_account_name", asset_id)
        await loop.run_in_executor(
            None,
            lambda a=acct_name: storage.storage_accounts.update(rg, a, {"allow_blob_public_access": True})
        )
    return {"action": "enable_public_blob_access", "assets": asset_ids, "public_access": True, "applied_at": datetime.now(timezone.utc).isoformat()}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "enable_public_blob_access", "assets": asset_ids, "public_access": True, "applied_at": datetime.now(timezone.utc).isoformat()}
    return await _real_execute(parameters, asset_ids, creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "enable_public_blob_access has no rollback"}
