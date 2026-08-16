# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""OCI Autonomous Database version upgrade executor.

Upgrades an Autonomous Database to a new database version.
Rollback is PARTIAL — ADB does not support version downgrades.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "partial"

_POLL_INTERVAL = 30
_POLL_TIMEOUT = 1800


async def _run(fn):
    return await asyncio.get_running_loop().run_in_executor(None, fn)


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = connector.credentials
    adb_id = parameters["adb_id"]
    target_version = parameters["target_version"]

    def _get():
        import oci
        from app.connectors.executors.oci._client import get_oci_config
        config = get_oci_config(creds)
        client = oci.database.DatabaseClient(config)
        return client.get_autonomous_database(adb_id).data

    adb = await _run(_get)
    current_version = adb.db_version

    if str(current_version) == str(target_version):
        return {"status": "already_at_version", "adb_id": adb_id,
                "previous_version": current_version, "current_version": current_version}

    def _upgrade():
        import oci
        from app.connectors.executors.oci._client import get_oci_config
        config = get_oci_config(creds)
        client = oci.database.DatabaseClient(config)
        details = oci.database.models.UpdateAutonomousDatabaseDetails(db_version=target_version)
        client.update_autonomous_database(adb_id, details)

    await _run(_upgrade)
    logger.info("oci_adb_upgrade: upgrade to %s initiated for %s", target_version, adb_id)

    deadline = time.time() + _POLL_TIMEOUT
    final_version = current_version
    while time.time() < deadline:
        await asyncio.sleep(_POLL_INTERVAL)

        def _poll():
            import oci
            from app.connectors.executors.oci._client import get_oci_config
            config = get_oci_config(creds)
            client = oci.database.DatabaseClient(config)
            return client.get_autonomous_database(adb_id).data

        polled = await _run(_poll)
        logger.info("oci_adb_upgrade: polling state=%s version=%s",
                    polled.lifecycle_state, polled.db_version)
        if polled.lifecycle_state == "AVAILABLE" and str(polled.db_version) == str(target_version):
            final_version = polled.db_version
            break
    else:
        raise TimeoutError(f"OCI ADB {adb_id} did not reach {target_version} within {_POLL_TIMEOUT}s")

    return {
        "status": "upgraded",
        "adb_id": adb_id,
        "previous_version": current_version,
        "current_version": final_version,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    adb_id = execution_result.get("adb_id", parameters.get("adb_id", "unknown"))
    prev = execution_result.get("previous_version", "unknown")
    return {
        "rolled_back": False,
        "reason": (
            f"OCI Autonomous Database does not support version downgrades. "
            f"ADB {adb_id} cannot be reverted to version {prev}. "
            f"Restore from an ADB backup to recover."
        ),
    }
