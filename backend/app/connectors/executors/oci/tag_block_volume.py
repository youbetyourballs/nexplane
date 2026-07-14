# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

from ._client import get_blockstorage_client
from app.services.pre_state_store import PreStateStore
from app.database import AsyncSessionLocal

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    volume_id = parameters.get("volume_id", "")
    freeform_tags = parameters.get("freeform_tags")
    defined_tags = parameters.get("defined_tags")

    if not creds:
        return {"action": "tag_block_volume", "volume_id": volume_id, "mock": True}

    import oci

    client = get_blockstorage_client(creds)
    loop = asyncio.get_running_loop()

    volume = await loop.run_in_executor(None, lambda: client.get_volume(volume_id=volume_id).data)
    prior_freeform = volume.freeform_tags or {}
    prior_defined = volume.defined_tags or {}

    async with AsyncSessionLocal() as db:
        PreStateStore.capture(
            db,
            parameters.get("cr_id"),
            parameters.get("step_id"),
            parameters.get("org_id"),
            {"freeform_tags": prior_freeform, "defined_tags": prior_defined},
        )
        await db.commit()

    new_freeform = freeform_tags if freeform_tags is not None else prior_freeform
    new_defined = defined_tags if defined_tags is not None else prior_defined

    await loop.run_in_executor(
        None,
        lambda: client.update_volume(
            volume_id=volume_id,
            update_volume_details=oci.core.models.UpdateVolumeDetails(
                freeform_tags=new_freeform,
                defined_tags=new_defined,
            ),
        ),
    )
    return {
        "action": "tag_block_volume",
        "volume_id": volume_id,
        "tagged": True,
        "tagged_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, asset_ids: list, connector, execution_result: dict) -> dict:
    creds = getattr(connector, "credentials", {})
    volume_id = parameters.get("volume_id", "")

    if not creds:
        return {"action": "rollback_tag_block_volume", "rolled_back": True, "mock": True}

    import oci

    client = get_blockstorage_client(creds)
    loop = asyncio.get_running_loop()

    async with AsyncSessionLocal() as db:
        state = await PreStateStore.retrieve(
            db,
            parameters.get("cr_id"),
            parameters.get("step_id"),
            parameters.get("org_id"),
        )

    if not state:
        return {"action": "rollback_tag_block_volume", "error": "pre-state not found", "rolled_back": False}

    await loop.run_in_executor(
        None,
        lambda: client.update_volume(
            volume_id=volume_id,
            update_volume_details=oci.core.models.UpdateVolumeDetails(
                freeform_tags=state.get("freeform_tags"),
                defined_tags=state.get("defined_tags"),
            ),
        ),
    )
    return {"action": "rollback_tag_block_volume", "volume_id": volume_id, "rolled_back": True}
