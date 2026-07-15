# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    server_name = parameters.get("server_name", "")
    db_name = parameters.get("database_name", "")
    rg = parameters.get("resource_group", creds.get("resource_group", "default"))
    location = parameters.get("location", "eastus")
    sku_name = parameters.get("sku_name", "Basic")

    if not creds:
        return {
            "action": "create_sql_database",
            "server_name": server_name,
            "database_name": db_name,
            "resource_group": rg,
            "mock": True,
        }

    from ._client import get_sql_client
    from azure.mgmt.sql.models import Database, Sku
    sql = get_sql_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        lambda: sql.databases.begin_create_or_update(
            rg, server_name, db_name,
            Database(location=location, sku=Sku(name=sku_name)),
        ).result(),
    )
    return {
        "action": "create_sql_database",
        "server_name": server_name,
        "database_name": db_name,
        "resource_group": rg,
        "location": location,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.azure.delete_sql_database import execute as delete
    return await delete(
        {
            "server_name": execution_result.get("server_name", parameters.get("server_name")),
            "database_name": execution_result.get("database_name", parameters.get("database_name")),
            "resource_group": execution_result.get("resource_group", parameters.get("resource_group")),
        },
        [], connector,
    )
