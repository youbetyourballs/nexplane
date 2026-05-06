import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    account = parameters.get("storage_account_name", "")
    container = parameters.get("container_name", "")
    rg = parameters.get("resource_group", creds.get("resource_group", "default"))

    if not creds:
        return {
            "action": "create_blob_container",
            "storage_account_name": account,
            "container_name": container,
            "resource_group": rg,
            "mock": True,
        }

    from ._client import get_storage_client
    storage = get_storage_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        lambda: storage.blob_containers.create(rg, account, container, {}),
    )
    return {
        "action": "create_blob_container",
        "storage_account_name": account,
        "container_name": container,
        "resource_group": rg,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.azure.delete_blob_container import execute as delete
    return await delete(
        {
            "storage_account_name": execution_result.get("storage_account_name", parameters.get("storage_account_name")),
            "container_name": execution_result.get("container_name", parameters.get("container_name")),
            "resource_group": execution_result.get("resource_group", parameters.get("resource_group")),
        },
        [], connector,
    )
