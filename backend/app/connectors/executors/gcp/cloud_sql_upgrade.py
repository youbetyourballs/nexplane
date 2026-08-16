# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""GCP Cloud SQL instance version upgrade executor.

Upgrades a Cloud SQL instance to a new database version (e.g. POSTGRES_14 → POSTGRES_15).
Rollback is PARTIAL — Cloud SQL does not support version downgrades.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "partial"

_POLL_INTERVAL = 30
_POLL_TIMEOUT = 3600


async def _run(fn):
    return await asyncio.get_running_loop().run_in_executor(None, fn)


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = connector.credentials
    instance_name = parameters["instance_name"]
    target_version = parameters["target_version"]

    def _get_project():
        from app.connectors.executors.gcp._client import get_project_id
        return get_project_id(creds)

    project_id = await _run(_get_project)

    def _get():
        from googleapiclient.discovery import build
        from app.connectors.executors.gcp._client import get_credentials
        credentials = get_credentials(creds)
        sql = build("sqladmin", "v1", credentials=credentials)
        return sql.instances().get(project=project_id, instance=instance_name).execute()

    inst = await _run(_get)
    current_version = inst.get("databaseVersion", "")

    if current_version == target_version:
        return {"status": "already_at_version", "instance_name": instance_name,
                "previous_version": current_version, "current_version": current_version}

    def _patch():
        from googleapiclient.discovery import build
        from app.connectors.executors.gcp._client import get_credentials
        credentials = get_credentials(creds)
        sql = build("sqladmin", "v1", credentials=credentials)
        return sql.instances().patch(
            project=project_id,
            instance=instance_name,
            body={"databaseVersion": target_version},
        ).execute()

    await _run(_patch)
    logger.info("cloud_sql_upgrade: upgrade to %s initiated for %s", target_version, instance_name)

    deadline = time.time() + _POLL_TIMEOUT
    final_version = current_version
    while time.time() < deadline:
        await asyncio.sleep(_POLL_INTERVAL)

        def _poll():
            from googleapiclient.discovery import build
            from app.connectors.executors.gcp._client import get_credentials
            credentials = get_credentials(creds)
            sql = build("sqladmin", "v1", credentials=credentials)
            return sql.instances().get(project=project_id, instance=instance_name).execute()

        polled = await _run(_poll)
        pstate = polled.get("state", "")
        pver = polled.get("databaseVersion", "")
        logger.info("cloud_sql_upgrade: polling state=%s version=%s", pstate, pver)
        if pstate == "RUNNABLE" and pver == target_version:
            final_version = pver
            break
    else:
        raise TimeoutError(f"Cloud SQL {instance_name} did not reach {target_version} within {_POLL_TIMEOUT}s")

    return {
        "status": "upgraded",
        "instance_name": instance_name,
        "previous_version": current_version,
        "current_version": final_version,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    instance_name = execution_result.get("instance_name", parameters.get("instance_name", "unknown"))
    prev = execution_result.get("previous_version", "unknown")
    return {
        "rolled_back": False,
        "reason": (
            f"Cloud SQL does not support database version downgrades. "
            f"Instance {instance_name} cannot be reverted to {prev} via API. "
            f"Restore from a Cloud SQL backup to recover."
        ),
    }
