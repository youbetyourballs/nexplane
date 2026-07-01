# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import time


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    instance_name = parameters["instance_name"]
    zone = parameters["zone"]
    target_state = parameters.get("target_state", "RUNNING").upper()
    timeout = parameters.get("timeout_seconds", 300)

    if not creds:
        return {
            "action": "wait_instance_state",
            "instance_name": instance_name,
            "zone": zone,
            "target_state": target_state,
            "reached": True,
        }

    from ._client import get_credentials, get_project_id
    from google.cloud import compute_v1

    credentials = get_credentials(creds)
    project = get_project_id(creds)
    client = compute_v1.InstancesClient(credentials=credentials)
    loop = asyncio.get_running_loop()

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        instance = await loop.run_in_executor(
            None, lambda: client.get(project=project, zone=zone, instance=instance_name)
        )
        if instance.status == target_state:
            return {
                "action": "wait_instance_state",
                "instance_name": instance_name,
                "zone": zone,
                "target_state": target_state,
                "reached": True,
            }
        await asyncio.sleep(10)

    raise RuntimeError(f"Timed out waiting for {instance_name} to reach {target_state}")


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "wait_instance_state is read-only"}
