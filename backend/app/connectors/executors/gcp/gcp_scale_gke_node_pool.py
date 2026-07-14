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
    node_count = parameters["node_count"]

    if not creds:
        return {
            "scaled": True,
            "new_count": node_count,
            "pre_state": {"node_count": 1},
            "cluster_name": cluster_name,
            "node_pool_name": node_pool_name,
            "mock": True,
        }

    project_id = get_project_id(creds)
    client = get_container_client(creds)
    loop = asyncio.get_event_loop()
    pool_ref = f"projects/{project_id}/locations/{location}/clusters/{cluster_name}/nodePools/{node_pool_name}"

    # Capture pre-state before scaling
    pool = await loop.run_in_executor(None, lambda: client.get_node_pool({"name": pool_ref}))
    prior_count = pool.initial_node_count if hasattr(pool, "initial_node_count") else 1

    op = await loop.run_in_executor(
        None,
        lambda: client.set_node_pool_size({
            "name": pool_ref,
            "node_count": node_count,
        })
    )
    await poll_gke_operation(client, op.name, timeout=600)

    return {
        "scaled": True,
        "new_count": node_count,
        "pre_state": {"node_count": prior_count},
        "cluster_name": cluster_name,
        "location": location,
        "node_pool_name": node_pool_name,
        "project_id": project_id,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    cluster_name = execution_result.get("cluster_name", parameters.get("cluster_name", ""))
    location = execution_result.get("location", parameters.get("location", ""))
    node_pool_name = execution_result.get("node_pool_name", parameters.get("node_pool_name", ""))
    project_id = execution_result.get("project_id", "")
    prior_count = execution_result.get("pre_state", {}).get("node_count", 1)

    if not creds:
        return {"rolled_back": True, "mock": True}

    if not project_id:
        project_id = get_project_id(creds)
    client = get_container_client(creds)
    loop = asyncio.get_event_loop()
    pool_ref = f"projects/{project_id}/locations/{location}/clusters/{cluster_name}/nodePools/{node_pool_name}"

    op = await loop.run_in_executor(
        None,
        lambda: client.set_node_pool_size({"name": pool_ref, "node_count": prior_count})
    )
    await poll_gke_operation(client, op.name, timeout=600)

    return {"rolled_back": True, "restored_count": prior_count, "node_pool_name": node_pool_name}
