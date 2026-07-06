# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import time


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    instance_name = parameters["instance_name"]
    database_version = parameters.get("database_version", "POSTGRES_14")
    tier = parameters.get("tier", "db-f1-micro")
    region = parameters.get("region", "us-central1")
    if not creds:
        return {
            "action": "create_cloudsql_instance",
            "instance_name": instance_name,
            "connection_name": f"mock-project:us-central1:{instance_name}",
        }
    from ._client import get_credentials, get_project_id
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()

    def _create_and_wait():
        from googleapiclient.discovery import build
        svc = build("sqladmin", "v1beta4", credentials=credentials)
        body = {
            "name": instance_name,
            "databaseVersion": database_version,
            "region": region,
            "settings": {
                "tier": tier,
                "backupConfiguration": {"enabled": True},
            },
        }
        op = svc.instances().insert(project=project, body=body).execute()
        op_name = op["name"]
        deadline = time.time() + 900
        while time.time() < deadline:
            time.sleep(15)
            op_status = svc.operations().get(project=project, operation=op_name).execute()
            if op_status["status"] == "DONE":
                if "error" in op_status:
                    raise RuntimeError(f"Cloud SQL create failed: {op_status['error']}")
                break
        else:
            raise TimeoutError(f"Cloud SQL instance {instance_name} did not become DONE within 900s")
        instance = svc.instances().get(project=project, instance=instance_name).execute()
        return instance.get("connectionName", f"{project}:{region}:{instance_name}")

    connection_name = await loop.run_in_executor(None, _create_and_wait)
    return {
        "action": "create_cloudsql_instance",
        "instance_name": instance_name,
        "connection_name": connection_name,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.gcp.delete_cloudsql_instance import execute as delete
    instance_name = execution_result.get("instance_name", parameters.get("instance_name"))
    return await delete({"instance_name": instance_name}, [], connector)
