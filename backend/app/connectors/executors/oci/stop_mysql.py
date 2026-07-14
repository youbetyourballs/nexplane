# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

ROLLBACK_CAPABILITY = "full"

import asyncio
import time
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    db_system_id = parameters.get("db_system_id", "")

    if not creds:
        return {"action": "stop_mysql", "db_system_id": db_system_id, "status": "INACTIVE", "mock": True}

    import oci
    from ._client import get_mysql_client
    mysql_client = get_mysql_client(creds)
    loop = asyncio.get_running_loop()

    def _stop_and_poll():
        details = oci.mysql.models.StopDbSystemDetails(
            shutdown_type=oci.mysql.models.StopDbSystemDetails.SHUTDOWN_TYPE_SLOW
        )
        mysql_client.stop_db_system(
            db_system_id=db_system_id,
            stop_db_system_details=details,
        )
        for _ in range(20):
            info = mysql_client.get_db_system(db_system_id=db_system_id).data
            if info.lifecycle_state == "INACTIVE":
                return info.lifecycle_state
            time.sleep(30)
        raise TimeoutError("MySQL DB System did not reach INACTIVE within 10 minutes")

    state = await loop.run_in_executor(None, _stop_and_poll)
    return {
        "action": "stop_mysql",
        "db_system_id": db_system_id,
        "status": state,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.oci.start_mysql import execute as start
    return await start(
        {"db_system_id": execution_result.get("db_system_id", parameters.get("db_system_id", ""))},
        [], connector,
    )
