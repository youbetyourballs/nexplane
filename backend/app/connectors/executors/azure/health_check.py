# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "read-only operation — no state was changed"

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    resource_group = parameters["resource_group"]
    vm_name = parameters["vm_name"]

    if not creds:
        return {
            "action": "health_check",
            "resource_group": resource_group,
            "vm_name": vm_name,
            "power_state": "PowerState/running",
        }

    from ._client import get_compute_client
    client = get_compute_client(creds)
    loop = asyncio.get_running_loop()
    vm = await loop.run_in_executor(
        None, lambda: client.virtual_machines.get(resource_group, vm_name, expand="instanceView")
    )

    statuses = vm.instance_view.statuses if vm.instance_view else []
    power_state = next(
        (s.code for s in statuses if s.code and s.code.startswith("PowerState/")),
        "PowerState/unknown",
    )

    if power_state != "PowerState/running":
        raise RuntimeError(f"VM {vm_name} is not running — power state: {power_state}")

    return {
        "action": "health_check",
        "resource_group": resource_group,
        "vm_name": vm_name,
        "power_state": power_state,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "health_check is read-only"}
