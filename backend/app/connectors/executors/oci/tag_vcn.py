# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

from ._client import get_network_client
from app.services.pre_state_store import PreStateStore
from app.database import AsyncSessionLocal

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    vcn_id = parameters.get("vcn_id", "")
    freeform_tags = parameters.get("freeform_tags")
    defined_tags = parameters.get("defined_tags")

    if not creds:
        return {"action": "tag_vcn", "vcn_id": vcn_id, "mock": True}

    import oci

    client = get_network_client(creds)
    loop = asyncio.get_running_loop()

    vcn = await loop.run_in_executor(None, lambda: client.get_vcn(vcn_id=vcn_id).data)
    prior_freeform = vcn.freeform_tags or {}
    prior_defined = vcn.defined_tags or {}

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
        lambda: client.update_vcn(
            vcn_id=vcn_id,
            update_vcn_details=oci.core.models.UpdateVcnDetails(
                freeform_tags=new_freeform,
                defined_tags=new_defined,
            ),
        ),
    )
    return {
        "action": "tag_vcn",
        "vcn_id": vcn_id,
        "tagged": True,
        "tagged_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    vcn_id = parameters.get("vcn_id", "")

    if not creds:
        return {"action": "rollback_tag_vcn", "rolled_back": True, "mock": True}

    import oci

    client = get_network_client(creds)
    loop = asyncio.get_running_loop()

    async with AsyncSessionLocal() as db:
        state = await PreStateStore.retrieve(
            db,
            parameters.get("cr_id"),
            parameters.get("step_id"),
            parameters.get("org_id"),
        )

    if not state:
        return {"action": "rollback_tag_vcn", "error": "pre-state not found", "rolled_back": False}

    await loop.run_in_executor(
        None,
        lambda: client.update_vcn(
            vcn_id=vcn_id,
            update_vcn_details=oci.core.models.UpdateVcnDetails(
                freeform_tags=state.get("freeform_tags"),
                defined_tags=state.get("defined_tags"),
            ),
        ),
    )
    return {"action": "rollback_tag_vcn", "vcn_id": vcn_id, "rolled_back": True}
