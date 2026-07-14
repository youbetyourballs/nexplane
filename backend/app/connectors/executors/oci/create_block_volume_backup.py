# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

ROLLBACK_CAPABILITY = "full"

import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    ts = int(datetime.now(timezone.utc).timestamp())
    volume_id = parameters.get("volume_id", "")
    display_name = parameters.get("display_name", f"nexplane-backup-{ts}")
    backup_type = parameters.get("type", "INCREMENTAL")

    if not creds:
        return {
            "action": "create_block_volume_backup",
            "volume_id": volume_id,
            "backup_id": "ocid1.volumebackup.oc1..mock",
            "display_name": display_name,
            "mock": True,
        }

    from ._client import get_blockstorage_client
    import oci
    client = get_blockstorage_client(creds)
    loop = asyncio.get_running_loop()

    def _call():
        details = oci.core.models.CreateVolumeBackupDetails(
            volume_id=volume_id,
            display_name=display_name,
            type=backup_type,
        )
        backup = client.create_volume_backup(create_volume_backup_details=details).data
        return backup

    backup = await loop.run_in_executor(None, _call)
    return {
        "action": "create_block_volume_backup",
        "volume_id": volume_id,
        "backup_id": backup.id,
        "display_name": display_name,
        "lifecycle_state": backup.lifecycle_state,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    backup_id = execution_result.get("backup_id", "")
    if not creds or not backup_id:
        return {"rolled_back": False, "reason": "no backup_id or no credentials"}

    from ._client import get_blockstorage_client
    client = get_blockstorage_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: client.delete_volume_backup(volume_backup_id=backup_id))
    return {"rolled_back": True, "backup_id": backup_id}
