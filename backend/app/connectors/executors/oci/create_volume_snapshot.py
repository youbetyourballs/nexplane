# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

ROLLBACK_CAPABILITY = "full"

import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    instance_id = parameters.get("instance_id", "")
    display_name = parameters.get("display_name", f"nexplane-snapshot-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}")
    backup_type = parameters.get("backup_type", "INCREMENTAL")

    if not creds:
        return {
            "action": "create_volume_snapshot",
            "instance_id": instance_id,
            "backup_id": "ocid1.bootvolume_backup.oc1..mock",
            "display_name": display_name,
            "backup_type": backup_type,
            "lifecycle_state": "AVAILABLE",
            "mock": True,
        }

    from ._client import get_compute_client, get_blockstorage_client
    import oci

    compute = get_compute_client(creds)
    blockstorage = get_blockstorage_client(creds)
    loop = asyncio.get_running_loop()

    instance = await loop.run_in_executor(None, lambda: compute.get_instance(instance_id).data)
    compartment_id = instance.compartment_id

    bv_attachments = await loop.run_in_executor(
        None,
        lambda: compute.list_boot_volume_attachments(
            availability_domain=instance.availability_domain,
            compartment_id=compartment_id,
            instance_id=instance_id,
        ).data,
    )
    if not bv_attachments:
        raise ValueError(f"No boot volume attachment found for instance {instance_id}.")

    boot_volume_id = bv_attachments[0].boot_volume_id

    details = oci.core.models.CreateBootVolumeBackupDetails(
        boot_volume_id=boot_volume_id,
        display_name=display_name,
        type=backup_type,
    )
    backup = await loop.run_in_executor(None, lambda: blockstorage.create_boot_volume_backup(details).data)

    return {
        "action": "create_volume_snapshot",
        "instance_id": instance_id,
        "boot_volume_id": boot_volume_id,
        "backup_id": backup.id,
        "display_name": display_name,
        "backup_type": backup_type,
        "lifecycle_state": backup.lifecycle_state,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    backup_id = execution_result.get("backup_id", "")
    if not backup_id or not creds:
        return {"rolled_back": False, "reason": "no backup_id to delete"}
    from ._client import get_blockstorage_client
    blockstorage = get_blockstorage_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: blockstorage.delete_boot_volume_backup(backup_id))
    return {"rolled_back": True, "backup_id": backup_id}
