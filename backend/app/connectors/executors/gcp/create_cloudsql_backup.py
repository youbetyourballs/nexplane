# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import time

ROLLBACK_CAPABILITY = "full"

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    instance_name = parameters["instance_name"]
    if not creds:
        return {"action": "create_cloudsql_backup", "instance_name": instance_name, "backup_run_id": 0}
    from ._client import get_credentials, get_project_id
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()

    def _backup_and_wait():
        from googleapiclient.discovery import build
        svc = build("sqladmin", "v1beta4", credentials=credentials)
        op = svc.backupRuns().insert(project=project, instance=instance_name, body={}).execute()
        op_name = op["name"]
        deadline = time.time() + 300
        while time.time() < deadline:
            time.sleep(10)
            op_status = svc.operations().get(project=project, operation=op_name).execute()
            if op_status["status"] == "DONE":
                if "error" in op_status:
                    raise RuntimeError(f"Cloud SQL backup failed: {op_status['error']}")
                break
        else:
            raise TimeoutError(f"Cloud SQL backup for {instance_name} did not complete within 300s")
        # Fetch the most recent backup run ID
        runs = svc.backupRuns().list(project=project, instance=instance_name).execute()
        items = runs.get("items", [])
        backup_run_id = items[0]["id"] if items else 0
        return backup_run_id

    backup_run_id = await loop.run_in_executor(None, _backup_and_wait)
    return {
        "action": "create_cloudsql_backup",
        "instance_name": instance_name,
        "backup_run_id": backup_run_id,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "backup is cleaned up automatically when instance is deleted"}
