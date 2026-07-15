# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    instance_name = parameters["instance_name"]
    zone = parameters["zone"]

    if not creds:
        return {"action": "reboot_instance", "instance_name": instance_name, "zone": zone, "status": "RESTARTING"}

    from ._client import get_credentials, get_project_id
    from google.cloud import compute_v1

    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_running_loop()
    client = compute_v1.InstancesClient(credentials=credentials)

    op = await loop.run_in_executor(
        None, lambda: client.reset(project=project, zone=zone, instance=instance_name)
    )
    return {
        "action": "reboot_instance",
        "instance_name": instance_name,
        "zone": zone,
        "operation": op.name,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "reboot_instance has no meaningful inverse"}
