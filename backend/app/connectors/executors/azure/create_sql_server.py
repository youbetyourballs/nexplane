import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    server_name = parameters.get("server_name", "")
    rg = parameters.get("resource_group", creds.get("resource_group", "default"))
    location = parameters.get("location", "eastus")
    admin_login = parameters.get("admin_login", "nexplaneadmin")
    admin_password = parameters.get("admin_password", "")

    if not creds:
        return {
            "action": "create_sql_server",
            "server_name": server_name,
            "resource_group": rg,
            "location": location,
            "mock": True,
        }

    from ._client import get_sql_client
    from azure.mgmt.sql.models import Server
    sql = get_sql_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        lambda: sql.servers.begin_create_or_update(
            rg, server_name,
            Server(
                location=location,
                administrator_login=admin_login,
                administrator_login_password=admin_password,
            ),
        ).result(),
    )
    return {
        "action": "create_sql_server",
        "server_name": server_name,
        "resource_group": rg,
        "location": location,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.azure.delete_sql_server import execute as delete
    return await delete(
        {
            "server_name": execution_result.get("server_name", parameters.get("server_name")),
            "resource_group": execution_result.get("resource_group", parameters.get("resource_group")),
        },
        [], connector,
    )
