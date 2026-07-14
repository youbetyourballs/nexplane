# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import time
from datetime import datetime, timezone

from ._client import get_database_client

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "Restoring an Autonomous Database overwrites current data; the prior state cannot be recovered after restore completes"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    db_id = parameters.get("autonomous_database_id", "")
    timestamp = parameters.get("timestamp", "")
    backup_id = parameters.get("backup_id", "")

    if not creds:
        return {"action": "restore_adb", "autonomous_database_id": db_id, "mock": True}

    import oci
    client = get_database_client(creds)
    loop = asyncio.get_running_loop()

    def _restore_and_poll():
        if backup_id:
            details = oci.database.models.RestoreAutonomousDatabaseDetails(
                database_backup_id=backup_id
            )
        else:
            details = oci.database.models.RestoreAutonomousDatabaseDetails(
                timestamp=timestamp
            )
        client.restore_autonomous_database(
            autonomous_database_id=db_id,
            restore_autonomous_database_details=details,
        )
        for _ in range(60):
            info = client.get_autonomous_database(autonomous_database_id=db_id).data
            if info.lifecycle_state == "AVAILABLE":
                return
            if info.lifecycle_state in ("FAILED", "TERMINATED"):
                raise RuntimeError(f"ADB restore failed: lifecycle_state={info.lifecycle_state}")
            time.sleep(30)
        raise TimeoutError("ADB restore did not complete within 30 minutes")

    await loop.run_in_executor(None, _restore_and_poll)
    return {
        "action": "restore_adb",
        "autonomous_database_id": db_id,
        "restored_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": ROLLBACK_REASON}
