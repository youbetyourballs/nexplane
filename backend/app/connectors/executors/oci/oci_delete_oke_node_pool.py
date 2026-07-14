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

    if not creds:
        return {
            "action": "delete_oke_node_pool",
            "node_pool_id": node_pool_id,
            "deleted": True,
            "drain_recommended": False,
            "active_node_count": 0,
            "mock": True,
        }

    client = get_container_engine_client(creds)
    loop = asyncio.get_running_loop()

    pool = await loop.run_in_executor(None, lambda: client.get_node_pool(node_pool_id).data)

    active_count = sum(
        1 for n in (pool.nodes or []) if n.lifecycle_state == "ACTIVE"
    )
    state = {
        "compartment_id": pool.compartment_id,
        "cluster_id": pool.cluster_id,
        "name": pool.name,
        "kubernetes_version": pool.kubernetes_version,
        "node_shape": pool.node_shape,
        "node_config_details": {
            "size": pool.node_config_details.size,
            "placement_configs": [
                {"availability_domain": pc.availability_domain, "subnet_id": pc.subnet_id}
                for pc in (pool.node_config_details.placement_configs or [])
            ],
        },
    }

    async with AsyncSessionLocal() as db:
        await PreStateStore.capture(
            db,
            parameters.get("cr_id"),
            parameters.get("step_id"),
            parameters.get("org_id"),
            state,
        )
        await db.commit()

    response = await loop.run_in_executor(None, lambda: client.delete_node_pool(node_pool_id))
    wr_id = response.headers.get("opc-work-request-id")
    if wr_id:
        try:
            await poll_work_request(client, wr_id, "nodepool", timeout=900)
        except RuntimeError as exc:
            if "but no nodepool resource found" not in str(exc):
                raise

    return {
        "action": "delete_oke_node_pool",
        "node_pool_id": node_pool_id,
        "deleted": True,
        "drain_recommended": active_count > 0,
        "active_node_count": active_count,
        "deleted_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    node_pool_id = parameters.get("node_pool_id", "")

    if not creds:
        return {"action": "rollback_delete_oke_node_pool", "rolled_back": True, "mock": True}

    import oci

    client = get_container_engine_client(creds)

    async with AsyncSessionLocal() as db:
        state = await PreStateStore.retrieve(
            db,
            parameters.get("cr_id"),
            parameters.get("step_id"),
            parameters.get("org_id"),
        )

    if not state:
        return {
            "action": "rollback_delete_oke_node_pool",
            "rolled_back": False,
            "error": "pre-state not found",
        }

    placement_configs = [
        oci.container_engine.models.NodePoolPlacementConfigDetails(
            availability_domain=pc["availability_domain"],
            subnet_id=pc["subnet_id"],
        )
        for pc in state["node_config_details"]["placement_configs"]
    ]
    details = oci.container_engine.models.CreateNodePoolDetails(
        compartment_id=state["compartment_id"],
        cluster_id=state["cluster_id"],
        name=state["name"],
        kubernetes_version=state["kubernetes_version"],
        node_shape=state["node_shape"],
        node_config_details=oci.container_engine.models.CreateNodePoolNodeConfigDetails(
            size=state["node_config_details"]["size"],
            placement_configs=placement_configs,
        ),
    )
    loop = asyncio.get_running_loop()
    response = await loop.run_in_executor(None, lambda: client.create_node_pool(details))
    wr_id = response.headers["opc-work-request-id"]
    new_pool_id = await poll_work_request(client, wr_id, "nodepool", timeout=900)

    return {
        "action": "rollback_delete_oke_node_pool",
        "original_node_pool_id": node_pool_id,
        "new_node_pool_id": new_pool_id,
        "rolled_back": True,
    }
