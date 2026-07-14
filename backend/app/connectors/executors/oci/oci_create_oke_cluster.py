# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

from ._client import get_container_engine_client
from ._oke_helpers import poll_work_request

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    compartment_id = parameters.get("compartment_id", "")
    name = parameters.get("name", "")
    vcn_id = parameters.get("vcn_id", "")
    kubernetes_version = parameters.get("kubernetes_version", "")
    subnet_ids = parameters.get("subnet_ids", [])
    node_shape = parameters.get("node_shape", "")
    node_count = parameters.get("node_count", 1)
    node_image_id = parameters.get("node_image_id")

    if not creds:
        return {
            "action": "create_oke_cluster",
            "cluster_id": "ocid1.cluster.mock",
            "node_pool_id": "ocid1.nodepool.mock",
            "mock": True,
        }

    import oci

    client = get_container_engine_client(creds)
    loop = asyncio.get_running_loop()

    cluster_details = oci.container_engine.models.CreateClusterDetails(
        name=name,
        compartment_id=compartment_id,
        vcn_id=vcn_id,
        kubernetes_version=kubernetes_version,
        options=oci.container_engine.models.ClusterCreateOptions(
            service_lb_subnet_ids=subnet_ids,
        ),
    )
    create_resp = await loop.run_in_executor(None, lambda: client.create_cluster(cluster_details))
    cluster_wr_id = create_resp.headers["opc-work-request-id"]
    cluster_id = await poll_work_request(client, cluster_wr_id, "cluster", timeout=1200)

    placement_config = oci.container_engine.models.NodePoolPlacementConfigDetails(
        availability_domain="AD-1",
        subnet_id=subnet_ids[0],
    )
    node_config = oci.container_engine.models.CreateNodePoolNodeConfigDetails(
        size=node_count,
        placement_configs=[placement_config],
    )
    np_kwargs = dict(
        compartment_id=compartment_id,
        cluster_id=cluster_id,
        name=f"{name}-pool",
        kubernetes_version=kubernetes_version,
        node_shape=node_shape,
        node_config_details=node_config,
    )
    if node_image_id:
        np_kwargs["node_source_details"] = oci.container_engine.models.NodeSourceViaImageDetails(
            image_id=node_image_id,
            source_type="IMAGE",
        )
    np_details = oci.container_engine.models.CreateNodePoolDetails(**np_kwargs)
    np_resp = await loop.run_in_executor(None, lambda: client.create_node_pool(np_details))
    np_wr_id = np_resp.headers["opc-work-request-id"]
    node_pool_id = await poll_work_request(client, np_wr_id, "nodepool", timeout=900)

    return {
        "action": "create_oke_cluster",
        "cluster_id": cluster_id,
        "node_pool_id": node_pool_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    cluster_id = execution_result.get("cluster_id", "")
    node_pool_id = execution_result.get("node_pool_id", "")

    if not creds:
        return {
            "action": "rollback_create_oke_cluster",
            "rolled_back": True,
            "mock": True,
            "cluster_id": cluster_id,
            "node_pool_id": node_pool_id,
        }

    client = get_container_engine_client(creds)

    loop = asyncio.get_running_loop()
    if node_pool_id:
        np_resp = await loop.run_in_executor(None, lambda: client.delete_node_pool(node_pool_id))
        np_wr_id = np_resp.headers.get("opc-work-request-id")
        if np_wr_id:
            await poll_work_request(client, np_wr_id, "nodepool", timeout=900)

    if cluster_id:
        cl_resp = await loop.run_in_executor(None, lambda: client.delete_cluster(cluster_id))
        cl_wr_id = cl_resp.headers.get("opc-work-request-id")
        if cl_wr_id:
            await poll_work_request(client, cl_wr_id, "cluster", timeout=1200)

    return {
        "action": "rollback_create_oke_cluster",
        "cluster_id": cluster_id,
        "node_pool_id": node_pool_id,
        "rolled_back": True,
    }
