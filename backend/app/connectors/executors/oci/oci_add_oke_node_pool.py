# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

from ._client import get_container_engine_client
from ._oke_helpers import poll_work_request

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    cluster_id = parameters.get("cluster_id", "")
    compartment_id = parameters.get("compartment_id", "")
    name = parameters.get("name", "")
    kubernetes_version = parameters.get("kubernetes_version", "")
    node_shape = parameters.get("node_shape", "")
    node_count = parameters.get("node_count", 1)
    subnet_id = parameters.get("subnet_id", "")
    node_image_id = parameters.get("node_image_id")

    if not creds:
        return {
            "action": "add_oke_node_pool",
            "node_pool_id": "ocid1.nodepool.mock",
            "cluster_id": cluster_id,
            "mock": True,
        }

    import oci

    client = get_container_engine_client(creds)
    loop = asyncio.get_running_loop()

    np_kwargs = dict(
        compartment_id=compartment_id,
        cluster_id=cluster_id,
        name=name,
        kubernetes_version=kubernetes_version,
        node_shape=node_shape,
        node_config_details=oci.container_engine.models.CreateNodePoolNodeConfigDetails(
            size=node_count,
            placement_configs=[
                oci.container_engine.models.NodePoolPlacementConfigDetails(
                    availability_domain="AD-1",
                    subnet_id=subnet_id,
                )
            ],
        ),
    )
    if node_image_id:
        np_kwargs["node_source_details"] = oci.container_engine.models.NodeSourceViaImageDetails(
            image_id=node_image_id,
            source_type="IMAGE",
        )
    details = oci.container_engine.models.CreateNodePoolDetails(**np_kwargs)
    response = await loop.run_in_executor(None, lambda: client.create_node_pool(details))
    wr_id = response.headers["opc-work-request-id"]
    node_pool_id = await poll_work_request(client, wr_id, "nodepool", timeout=900)

    return {
        "action": "add_oke_node_pool",
        "cluster_id": cluster_id,
        "node_pool_id": node_pool_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    node_pool_id = execution_result.get("node_pool_id", "")

    if not creds:
        return {"action": "rollback_add_oke_node_pool", "rolled_back": True, "mock": True}

    client = get_container_engine_client(creds)
    loop = asyncio.get_running_loop()
    response = await loop.run_in_executor(None, lambda: client.delete_node_pool(node_pool_id))
    wr_id = response.headers.get("opc-work-request-id")
    if wr_id:
        try:
            await poll_work_request(client, wr_id, "nodepool", timeout=900)
        except RuntimeError as exc:
            if "but no nodepool resource found" not in str(exc):
                raise

    return {
        "action": "rollback_add_oke_node_pool",
        "node_pool_id": node_pool_id,
        "rolled_back": True,
    }
