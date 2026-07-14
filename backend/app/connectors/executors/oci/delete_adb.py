# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "Autonomous Database deletion is permanent; OCI does not support recreation from delete alone"

import asyncio
import time
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    db_id = parameters.get("db_id", "")

    if not creds:
        return {"action": "delete_adb", "db_id": db_id, "status": "TERMINATED", "mock": True}

    from ._client import get_database_client
    db_client = get_database_client(creds)
    loop = asyncio.get_running_loop()

    def _delete_and_poll():
        db_client.delete_autonomous_database(autonomous_database_id=db_id)
        for _ in range(20):
            try:
                info = db_client.get_autonomous_database(autonomous_database_id=db_id).data
                if info.lifecycle_state == "TERMINATED":
                    return "TERMINATED"
            except Exception:
                # 404 means deleted
                return "TERMINATED"
            time.sleep(30)
        raise TimeoutError("ADB did not reach TERMINATED within 10 minutes")

    state = await loop.run_in_executor(None, _delete_and_poll)
    return {
        "action": "delete_adb",
        "db_id": db_id,
        "status": state,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "delete_adb is destructive; Autonomous Database cannot be recovered after deletion"}
