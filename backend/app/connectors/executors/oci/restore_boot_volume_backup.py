# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import time
from datetime import datetime, timezone

from ._client import get_blockstorage_client

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    boot_volume_backup_id = parameters.get("boot_volume_backup_id", "")
    display_name = parameters.get("display_name", f"nexplane-restore-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}")
    compartment_id = parameters.get("compartment_id", "")
    availability_domain = parameters.get("availability_domain", "")

    if not creds:
        return {
            "action": "restore_boot_volume_backup",
            "boot_volume_id": "mock-boot-volume-id",
            "mock": True,
        }

    import oci
    client = get_blockstorage_client(creds)
    loop = asyncio.get_running_loop()

    def _create_and_poll():
        source = oci.core.models.BootVolumeSourceFromBootVolumeBackupDetails(id=boot_volume_backup_id)
        details = oci.core.models.CreateBootVolumeDetails(
            compartment_id=compartment_id,
            display_name=display_name,
            availability_domain=availability_domain,
            source_details=source,
        )
        vol = client.create_boot_volume(create_boot_volume_details=details).data
        for _ in range(60):
            info = client.get_boot_volume(boot_volume_id=vol.id).data
            if info.lifecycle_state == "AVAILABLE":
                return vol.id
            if info.lifecycle_state == "FAULTY":
                raise RuntimeError("Boot volume creation from backup failed")
            time.sleep(10)
        raise TimeoutError("Boot volume did not become AVAILABLE within 10 minutes")

    boot_volume_id = await loop.run_in_executor(None, _create_and_poll)
    return {
        "action": "restore_boot_volume_backup",
        "boot_volume_id": boot_volume_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    boot_volume_id = execution_result.get("boot_volume_id", "")
    if not creds or not boot_volume_id:
        return {"action": "rollback_restore_boot_volume_backup", "mock": True}

    client = get_blockstorage_client(creds)
    loop = asyncio.get_running_loop()

    def _delete_and_poll():
        client.delete_boot_volume(boot_volume_id=boot_volume_id)
        for _ in range(30):
            try:
                info = client.get_boot_volume(boot_volume_id=boot_volume_id).data
                if info.lifecycle_state == "TERMINATED":
                    return
            except Exception:
                return
            time.sleep(10)

    await loop.run_in_executor(None, _delete_and_poll)
    return {"action": "rollback_restore_boot_volume_backup", "boot_volume_id": boot_volume_id, "rolled_back": True}
