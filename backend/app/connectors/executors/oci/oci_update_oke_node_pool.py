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
    new_name = parameters.get("name")
    new_labels = parameters.get("initial_node_labels")

    if not creds:
        return {
            "action": "update_oke_node_pool",
            "node_pool_id": node_pool_id,
            "updated": True,
            "mock": True,
        }

    import oci

    client = get_container_engine_client(creds)
    loop = asyncio.get_running_loop()

    pool = await loop.run_in_executor(None, lambda: client.get_node_pool(node_pool_id).data)

    async with AsyncSessionLocal() as db:
        await PreStateStore.capture(
            db,
            parameters.get("cr_id"),
            parameters.get("step_id"),
            parameters.get("org_id"),
            {
                "name": pool.name,
                "initial_node_labels": [
                    {"key": lbl.key, "value": lbl.value}
                    for lbl in (pool.initial_node_labels or [])
                ],
            },
        )
        await db.commit()

    update_kwargs = {}
    if new_name is not None:
        update_kwargs["name"] = new_name
    if new_labels is not None:
        update_kwargs["initial_node_labels"] = [
            oci.container_engine.models.KeyValue(key=lbl["key"], value=lbl["value"])
            for lbl in new_labels
        ]

    response = await loop.run_in_executor(
        None,
        lambda: client.update_node_pool(
            node_pool_id,
            oci.container_engine.models.UpdateNodePoolDetails(**update_kwargs),
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
        "action": "update_oke_node_pool",
        "node_pool_id": node_pool_id,
        "updated": True,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    node_pool_id = parameters.get("node_pool_id", "")

    if not creds:
        return {"action": "rollback_update_oke_node_pool", "rolled_back": True, "mock": True}

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
        return {"action": "rollback_update_oke_node_pool", "rolled_back": False, "error": "pre-state not found"}

    update_kwargs = {"name": state["name"]}
    if state.get("initial_node_labels") is not None:
        update_kwargs["initial_node_labels"] = [
            oci.container_engine.models.KeyValue(key=lbl["key"], value=lbl["value"])
            for lbl in state["initial_node_labels"]
        ]

    response = await loop.run_in_executor(
        None,
        lambda: client.update_node_pool(
            node_pool_id,
            oci.container_engine.models.UpdateNodePoolDetails(**update_kwargs),
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
        "action": "rollback_update_oke_node_pool",
        "node_pool_id": node_pool_id,
        "rolled_back": True,
    }
