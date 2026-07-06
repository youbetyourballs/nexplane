# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import time


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    instance_name = parameters["instance_name"]
    if not creds:
        return {"action": "delete_cloudsql_instance", "instance_name": instance_name, "deleted": True}
    from ._client import get_credentials, get_project_id
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()

    def _delete_and_wait():
        from googleapiclient.discovery import build
        svc = build("sqladmin", "v1beta4", credentials=credentials)
        op = svc.instances().delete(project=project, instance=instance_name).execute()
        op_name = op["name"]
        deadline = time.time() + 600
        while time.time() < deadline:
            time.sleep(15)
            op_status = svc.operations().get(project=project, operation=op_name).execute()
            if op_status["status"] == "DONE":
                if "error" in op_status:
                    raise RuntimeError(f"Cloud SQL delete failed: {op_status['error']}")
                break
        else:
            raise TimeoutError(f"Cloud SQL delete for {instance_name} did not complete within 600s")

    await loop.run_in_executor(None, _delete_and_wait)
    return {"action": "delete_cloudsql_instance", "instance_name": instance_name, "deleted": True}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "Cloud SQL instance deletion is irreversible"}
