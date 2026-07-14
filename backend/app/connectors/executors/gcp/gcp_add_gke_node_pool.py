# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

from ._client import get_container_client, get_project_id
from ._gke_helpers import poll_gke_operation

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    cluster_name = parameters["cluster_name"]
    location = parameters["location"]
    node_pool_name = parameters["node_pool_name"]
    node_count = parameters.get("node_count", 1)
    machine_type = parameters.get("machine_type", "e2-medium")

    if not creds:
        return {
            "node_pool_name": node_pool_name,
            "cluster_name": cluster_name,
            "location": location,
            "mock": True,
        }

    from google.cloud import container_v1

    project_id = get_project_id(creds)
    client = get_container_client(creds)
    loop = asyncio.get_event_loop()
    cluster_ref = f"projects/{project_id}/locations/{location}/clusters/{cluster_name}"

    node_pool_body = container_v1.NodePool(
        name=node_pool_name,
        initial_node_count=node_count,
        config=container_v1.NodeConfig(
            machine_type=machine_type,
            oauth_scopes=["https://www.googleapis.com/auth/cloud-platform"],
        ),
    )
    request = container_v1.CreateNodePoolRequest(parent=cluster_ref, node_pool=node_pool_body)
    op = await loop.run_in_executor(None, lambda: client.create_node_pool(request))
    await poll_gke_operation(client, op.name, timeout=900)

    return {
        "node_pool_name": node_pool_name,
        "cluster_name": cluster_name,
        "location": location,
        "project_id": project_id,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    node_pool_name = execution_result.get("node_pool_name", parameters.get("node_pool_name", ""))
    cluster_name = execution_result.get("cluster_name", parameters.get("cluster_name", ""))
    location = execution_result.get("location", parameters.get("location", ""))
    project_id = execution_result.get("project_id", "")

    if not creds:
        return {"rolled_back": True, "mock": True}

    if not project_id:
        project_id = get_project_id(creds)
    client = get_container_client(creds)
    loop = asyncio.get_event_loop()
    pool_ref = f"projects/{project_id}/locations/{location}/clusters/{cluster_name}/nodePools/{node_pool_name}"

    op = await loop.run_in_executor(None, lambda: client.delete_node_pool({"name": pool_ref}))
    await poll_gke_operation(client, op.name, timeout=600)

    return {"rolled_back": True, "node_pool_name": node_pool_name}
