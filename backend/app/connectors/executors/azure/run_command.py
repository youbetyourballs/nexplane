# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "Azure VM run-command side effects on the target cannot be automatically undone"

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    resource_group = parameters["resource_group"]
    vm_name = parameters["vm_name"]
    command = parameters["command"]

    if not creds:
        return {
            "action": "run_command",
            "resource_group": resource_group,
            "vm_name": vm_name,
            "command": command,
            "stdout": "mock_output_ok",
            "stderr": "",
            "mock": True,
        }

    from ._client import get_compute_client
    from azure.mgmt.compute.models import RunCommandInput
    client = get_compute_client(creds)
    loop = asyncio.get_running_loop()

    result = await loop.run_in_executor(
        None,
        lambda: client.virtual_machines.begin_run_command(
            resource_group,
            vm_name,
            RunCommandInput(command_id="RunShellScript", script=[command]),
        ).result(),
    )

    stdout = ""
    stderr = ""
    if result and result.value:
        stdout = result.value[0].message if result.value[0].message else ""
    if result and len(result.value) > 1:
        stderr = result.value[1].message if result.value[1].message else ""

    return {
        "action": "run_command",
        "resource_group": resource_group,
        "vm_name": vm_name,
        "command": command,
        "stdout": stdout,
        "stderr": stderr,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "run_command has no meaningful inverse"}
