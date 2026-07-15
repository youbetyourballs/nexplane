# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "read-only operation — no state was changed"

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    instance_name = parameters["instance_name"]
    zone = parameters["zone"]

    if not creds:
        return {"action": "health_check", "instance_name": instance_name, "zone": zone, "status": "RUNNING"}

    from ._client import get_credentials, get_project_id
    from google.cloud import compute_v1

    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_running_loop()
    client = compute_v1.InstancesClient(credentials=credentials)

    instance = await loop.run_in_executor(
        None, lambda: client.get(project=project, zone=zone, instance=instance_name)
    )
    if instance.status != "RUNNING":
        raise RuntimeError(f"Instance {instance_name} is not RUNNING — status: {instance.status}")
    return {"action": "health_check", "instance_name": instance_name, "zone": zone, "status": instance.status}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "health_check is read-only"}
