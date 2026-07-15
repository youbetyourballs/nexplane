# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "read-only operation — no state was changed"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    resource_group = parameters["resource_group"]
    vm_name = parameters["vm_name"]

    if not creds:
        return {
            "action": "capture_vm_state",
            "resource_group": resource_group,
            "vm_name": vm_name,
            "vm_size": "Standard_B1s",
            "location": "eastus",
            "os_disk_name": f"{vm_name}-osdisk",
            "tags": {},
            "mock": True,
        }

    from ._client import get_compute_client
    client = get_compute_client(creds)
    loop = asyncio.get_running_loop()
    vm = await loop.run_in_executor(None, lambda: client.virtual_machines.get(resource_group, vm_name))

    os_disk_name = ""
    if vm.storage_profile and vm.storage_profile.os_disk:
        os_disk_name = vm.storage_profile.os_disk.name or ""

    return {
        "action": "capture_vm_state",
        "resource_group": resource_group,
        "vm_name": vm_name,
        "vm_size": vm.hardware_profile.vm_size if vm.hardware_profile else "unknown",
        "location": vm.location,
        "os_disk_name": os_disk_name,
        "tags": dict(vm.tags or {}),
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "capture_vm_state is read-only"}
