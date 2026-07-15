# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    resource_group = parameters["resource_group"]
    vm_name = parameters["vm_name"]

    if not creds:
        return {"action": "terminate_vm", "resource_group": resource_group, "vm_name": vm_name, "deleted": True, "mock": True}

    from ._client import get_compute_client, get_network_client
    compute = get_compute_client(creds)
    network = get_network_client(creds)
    loop = asyncio.get_running_loop()

    # Delete VM
    await loop.run_in_executor(
        None, lambda: compute.virtual_machines.begin_delete(resource_group, vm_name).result()
    )

    # Clean up networking resources created by launch_vm (named {vm_name}-*)
    for cleanup in [
        lambda: network.network_interfaces.begin_delete(resource_group, f"{vm_name}-nic").result(),
        lambda: network.public_ip_addresses.begin_delete(resource_group, f"{vm_name}-pip").result(),
        lambda: network.virtual_networks.begin_delete(resource_group, f"{vm_name}-vnet").result(),
    ]:
        try:
            await loop.run_in_executor(None, cleanup)
        except Exception:
            pass  # resources may not exist (e.g., user-provided VNet)

    return {
        "action": "terminate_vm",
        "resource_group": resource_group,
        "vm_name": vm_name,
        "deleted": True,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "terminate_vm is irreversible"}
