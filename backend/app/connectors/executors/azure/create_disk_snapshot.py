# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    resource_group = parameters["resource_group"]
    vm_name = parameters["vm_name"]
    snapshot_name = parameters.get("snapshot_name", f"nexplane-snap-{vm_name}")

    if not creds:
        return {
            "action": "create_disk_snapshot",
            "resource_group": resource_group,
            "vm_name": vm_name,
            "snapshot_name": snapshot_name,
            "disk_name": f"{vm_name}-osdisk",
            "mock": True,
        }

    from ._client import get_compute_client
    from azure.mgmt.compute.models import Snapshot, CreationData, DiskCreateOption
    client = get_compute_client(creds)
    loop = asyncio.get_running_loop()

    # Get the OS disk resource ID
    vm = await loop.run_in_executor(None, lambda: client.virtual_machines.get(resource_group, vm_name))
    disk_id = vm.storage_profile.os_disk.managed_disk.id
    disk_name = vm.storage_profile.os_disk.name
    location = vm.location

    snapshot = await loop.run_in_executor(
        None,
        lambda: client.snapshots.begin_create_or_update(
            resource_group,
            snapshot_name,
            Snapshot(
                location=location,
                creation_data=CreationData(
                    create_option=DiskCreateOption.copy,
                    source_resource_id=disk_id,
                ),
                tags={"managed-by": "nexplane", "source-vm": vm_name},
            ),
        ).result(),
    )

    return {
        "action": "create_disk_snapshot",
        "resource_group": resource_group,
        "vm_name": vm_name,
        "snapshot_name": snapshot_name,
        "disk_name": disk_name,
        "snapshot_id": snapshot.id,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.azure.delete_disk_snapshot import execute as delete_snap
    return await delete_snap(
        {
            "resource_group": execution_result.get("resource_group", parameters.get("resource_group")),
            "snapshot_name": execution_result.get("snapshot_name", parameters.get("snapshot_name")),
        },
        [], connector,
    )
