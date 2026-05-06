import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    server_name = parameters.get("server_name", "")
    db_name = parameters.get("database_name", "")
    rg = parameters.get("resource_group", creds.get("resource_group", "default"))

    if not creds:
        return {
            "action": "delete_sql_database",
            "server_name": server_name,
            "database_name": db_name,
            "resource_group": rg,
            "mock": True,
        }

    from ._client import get_sql_client
    sql = get_sql_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: sql.databases.begin_delete(rg, server_name, db_name).result())
    return {
        "action": "delete_sql_database",
        "server_name": server_name,
        "database_name": db_name,
        "resource_group": rg,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "SQL database deletion cannot be reversed automatically"}
