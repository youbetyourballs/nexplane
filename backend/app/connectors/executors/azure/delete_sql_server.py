# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    server_name = parameters.get("server_name", "")
    rg = parameters.get("resource_group", creds.get("resource_group", "default"))

    if not creds:
        return {"action": "delete_sql_server", "server_name": server_name, "resource_group": rg, "mock": True}

    from ._client import get_sql_client
    sql = get_sql_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: sql.servers.begin_delete(rg, server_name).result())
    return {
        "action": "delete_sql_server",
        "server_name": server_name,
        "resource_group": rg,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "SQL server deletion cannot be reversed automatically"}
