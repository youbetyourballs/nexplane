# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from ._client import get_container_client
from ._gke_helpers import poll_gke_operation

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    cluster_name = parameters["cluster_name"]
    location = parameters["location"]
    node_count = parameters.get("node_count", 1)
    machine_type = parameters.get("machine_type", "e2-medium")
    disk_size_gb = parameters.get("disk_size_gb", 100)
    network = parameters.get("network", "default")
    subnetwork = parameters.get("subnetwork", "")
    node_pool_name = "default-pool"

    if not creds:
        return {
            "cluster_name": cluster_name,
            "location": location,
            "node_pool_name": node_pool_name,
            "project_id": "mock-project",
            "mock": True,
        }

    from ._client import get_project_id
    from google.cloud import container_v1

    project_id = get_project_id(creds)
    client = get_container_client(creds)
    loop = asyncio.get_event_loop()
    parent = f"projects/{project_id}/locations/{location}"

    cluster_body = container_v1.Cluster(
        name=cluster_name,
        initial_node_count=0,
        network=network,
        subnetwork=subnetwork if subnetwork else "",
        node_pools=[],
    )
    create_request = container_v1.CreateClusterRequest(parent=parent, cluster=cluster_body)
    op = await loop.run_in_executor(None, lambda: client.create_cluster(create_request))
    await poll_gke_operation(client, op.name, timeout=1200)

    node_pool_body = container_v1.NodePool(
        name=node_pool_name,
        initial_node_count=node_count,
        config=container_v1.NodeConfig(
            machine_type=machine_type,
            disk_size_gb=disk_size_gb,
            oauth_scopes=["https://www.googleapis.com/auth/cloud-platform"],
        ),
    )
    cluster_ref = f"{parent}/clusters/{cluster_name}"
    np_request = container_v1.CreateNodePoolRequest(parent=cluster_ref, node_pool=node_pool_body)
    op2 = await loop.run_in_executor(None, lambda: client.create_node_pool(np_request))
    await poll_gke_operation(client, op2.name, timeout=900)

    return {
        "cluster_name": cluster_name,
        "location": location,
        "node_pool_name": node_pool_name,
        "project_id": project_id,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    cluster_name = execution_result.get("cluster_name", parameters.get("cluster_name", ""))
    location = execution_result.get("location", parameters.get("location", ""))
    project_id = execution_result.get("project_id", "")

    if not creds:
        return {"rolled_back": True, "mock": True}

    from ._client import get_project_id

    if not project_id:
        project_id = get_project_id(creds)
    client = get_container_client(creds)
    loop = asyncio.get_event_loop()
    cluster_ref = f"projects/{project_id}/locations/{location}/clusters/{cluster_name}"

    op = await loop.run_in_executor(None, lambda: client.delete_cluster({"name": cluster_ref}))
    await poll_gke_operation(client, op.name, timeout=1200)

    return {"rolled_back": True, "cluster_name": cluster_name, "location": location}
