# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import uuid as _uuid
from datetime import datetime, timezone

from ._client import get_compute_client
from app.services.pre_state_store import PreStateStore
from app.database import AsyncSessionLocal

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    instance_id = parameters.get("instance_id", "")
    freeform_tags = parameters.get("freeform_tags")
    defined_tags = parameters.get("defined_tags")

    if not creds:
        return {"action": "tag_compute_instance", "instance_id": instance_id, "mock": True}

    import oci

    client = get_compute_client(creds)
    loop = asyncio.get_running_loop()

    instance = await loop.run_in_executor(None, lambda: client.get_instance(instance_id=instance_id).data)
    prior_freeform = instance.freeform_tags or {}
    prior_defined = instance.defined_tags or {}

    async with AsyncSessionLocal() as db:
        await PreStateStore.capture(
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
        lambda: client.update_instance(
            instance_id=instance_id,
            update_instance_details=oci.core.models.UpdateInstanceDetails(
                freeform_tags=new_freeform,
                defined_tags=new_defined,
            ),
        ),
    )
    return {
        "action": "tag_compute_instance",
        "instance_id": instance_id,
        "tagged": True,
        "tagged_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    instance_id = parameters.get("instance_id", "")

    if not creds:
        return {"action": "rollback_tag_compute_instance", "rolled_back": True, "mock": True}

    import oci

    client = get_compute_client(creds)
    loop = asyncio.get_running_loop()

    async with AsyncSessionLocal() as db:
        state = await PreStateStore.retrieve(
            db,
            parameters.get("cr_id"),
            parameters.get("step_id"),
            parameters.get("org_id"),
        )

    if not state:
        return {"action": "rollback_tag_compute_instance", "error": "pre-state not found", "rolled_back": False}

    await loop.run_in_executor(
        None,
        lambda: client.update_instance(
            instance_id=instance_id,
            update_instance_details=oci.core.models.UpdateInstanceDetails(
                freeform_tags=state.get("freeform_tags"),
                defined_tags=state.get("defined_tags"),
            ),
        ),
    )
    return {"action": "rollback_tag_compute_instance", "instance_id": instance_id, "rolled_back": True}
