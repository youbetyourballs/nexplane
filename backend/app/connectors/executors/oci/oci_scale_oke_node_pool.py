# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

from ._client import get_container_engine_client
from ._oke_helpers import poll_work_request
from app.services.pre_state_store import PreStateStore
from app.database import AsyncSessionLocal

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    node_pool_id = parameters.get("node_pool_id", "")
    new_count = parameters.get("node_count", 1)

    if not creds:
        return {
            "action": "scale_oke_node_pool",
            "node_pool_id": node_pool_id,
            "scaled": True,
            "mock": True,
        }

    import oci

    client = get_container_engine_client(creds)
    loop = asyncio.get_running_loop()

    pool = await loop.run_in_executor(None, lambda: client.get_node_pool(node_pool_id).data)
    prior_count = pool.node_config_details.size

    async with AsyncSessionLocal() as db:
        await PreStateStore.capture(
            db,
            parameters.get("cr_id"),
            parameters.get("step_id"),
            parameters.get("org_id"),
            {"node_count": prior_count},
        )
        await db.commit()

    response = await loop.run_in_executor(
        None,
        lambda: client.update_node_pool(
            node_pool_id,
            oci.container_engine.models.UpdateNodePoolDetails(
                node_config_details=oci.container_engine.models.UpdateNodePoolNodeConfigDetails(
                    size=new_count,
                )
            ),
        ),
    )
    wr_id = response.headers.get("opc-work-request-id")
    if wr_id:
        try:
            await poll_work_request(client, wr_id, "nodepool", timeout=600)
        except RuntimeError as e:
            if "no nodepool resource found" in str(e):
                pass
            else:
                raise

    return {
        "action": "scale_oke_node_pool",
        "node_pool_id": node_pool_id,
        "previous_count": prior_count,
        "new_count": new_count,
        "scaled": True,
        "scaled_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    node_pool_id = parameters.get("node_pool_id", "")

    if not creds:
        return {"action": "rollback_scale_oke_node_pool", "rolled_back": True, "mock": True}

    import oci

    client = get_container_engine_client(creds)
    loop = asyncio.get_running_loop()

    async with AsyncSessionLocal() as db:
        state = await PreStateStore.retrieve(
            db,
            parameters.get("cr_id"),
            parameters.get("step_id"),
            parameters.get("org_id"),
        )

    if not state:
        return {"action": "rollback_scale_oke_node_pool", "rolled_back": False, "error": "pre-state not found"}

    prior_count = state["node_count"]
    response = await loop.run_in_executor(
        None,
        lambda: client.update_node_pool(
            node_pool_id,
            oci.container_engine.models.UpdateNodePoolDetails(
                node_config_details=oci.container_engine.models.UpdateNodePoolNodeConfigDetails(
                    size=prior_count,
                )
            ),
        ),
    )
    wr_id = response.headers.get("opc-work-request-id")
    if wr_id:
        try:
            await poll_work_request(client, wr_id, "nodepool", timeout=600)
        except RuntimeError as e:
            if "no nodepool resource found" in str(e):
                pass
            else:
                raise

    return {
        "action": "rollback_scale_oke_node_pool",
        "node_pool_id": node_pool_id,
        "restored_count": prior_count,
        "rolled_back": True,
    }
