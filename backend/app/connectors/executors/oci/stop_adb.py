# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import time
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    db_id = parameters.get("db_id", "")

    if not creds:
        return {"action": "stop_adb", "db_id": db_id, "status": "STOPPED", "mock": True}

    from ._client import get_database_client
    db_client = get_database_client(creds)
    loop = asyncio.get_running_loop()

    def _stop_and_poll():
        db_client.stop_autonomous_database(autonomous_database_id=db_id)
        for _ in range(20):
            info = db_client.get_autonomous_database(autonomous_database_id=db_id).data
            if info.lifecycle_state == "STOPPED":
                return info.lifecycle_state
            time.sleep(30)
        raise TimeoutError("ADB did not reach STOPPED within 10 minutes")

    state = await loop.run_in_executor(None, _stop_and_poll)
    return {
        "action": "stop_adb",
        "db_id": db_id,
        "status": state,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.oci.start_adb import execute as start
    return await start({"db_id": execution_result.get("db_id", parameters.get("db_id", ""))}, [], connector)
