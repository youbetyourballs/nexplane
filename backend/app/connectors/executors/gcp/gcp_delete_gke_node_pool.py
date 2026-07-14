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

    if not creds:
        return {
            "deleted": True,
            "drain_recommended": False,
            "active_node_count": 0,
            "cluster_name": cluster_name,
            "node_pool_name": node_pool_name,
            "mock": True,
        }

    project_id = get_project_id(creds)
    client = get_container_client(creds)
    loop = asyncio.get_event_loop()
    pool_ref = f"projects/{project_id}/locations/{location}/clusters/{cluster_name}/nodePools/{node_pool_name}"

    # Capture pre-state BEFORE any destructive call
    pool = await loop.run_in_executor(None, lambda: client.get_node_pool({"name": pool_ref}))
    active_node_count = len(pool.instance_group_urls) if pool.instance_group_urls else 0
    drain_recommended = active_node_count > 0

    pre_state = {
        "node_pool_name": node_pool_name,
        "node_count": pool.initial_node_count if hasattr(pool, "initial_node_count") else 1,
        "machine_type": pool.config.machine_type if (pool.config and pool.config.machine_type) else "e2-medium",
    }

    op = await loop.run_in_executor(None, lambda: client.delete_node_pool({"name": pool_ref}))
    await poll_gke_operation(client, op.name, timeout=600)

    return {
        "deleted": True,
        "drain_recommended": drain_recommended,
        "active_node_count": active_node_count,
        "cluster_name": cluster_name,
        "location": location,
        "node_pool_name": node_pool_name,
        "project_id": project_id,
        "pre_state": pre_state,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    cluster_name = execution_result.get("cluster_name", parameters.get("cluster_name", ""))
    location = execution_result.get("location", parameters.get("location", ""))
    project_id = execution_result.get("project_id", "")
    pre_state = execution_result.get("pre_state", {})
    node_pool_name = pre_state.get("node_pool_name", parameters.get("node_pool_name", ""))
    node_count = pre_state.get("node_count", 1)
    machine_type = pre_state.get("machine_type", "e2-medium")

    if not creds:
        return {"rolled_back": True, "mock": True}

    from google.cloud import container_v1

    if not project_id:
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

    return {"rolled_back": True, "node_pool_name": node_pool_name}
