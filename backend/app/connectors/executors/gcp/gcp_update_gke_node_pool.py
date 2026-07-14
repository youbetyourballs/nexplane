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
    new_display_name = parameters.get("display_name")
    new_labels = parameters.get("labels")

    if not creds:
        return {
            "updated": True,
            "pre_state": {"display_name": node_pool_name, "labels": {}},
            "cluster_name": cluster_name,
            "node_pool_name": node_pool_name,
            "mock": True,
        }

    from google.cloud import container_v1

    project_id = get_project_id(creds)
    client = get_container_client(creds)
    loop = asyncio.get_event_loop()
    pool_ref = f"projects/{project_id}/locations/{location}/clusters/{cluster_name}/nodePools/{node_pool_name}"

    # Capture pre-state before updating
    pool = await loop.run_in_executor(None, lambda: client.get_node_pool({"name": pool_ref}))
    prior_labels = dict(pool.config.labels) if (pool.config and pool.config.labels) else {}
    prior_display_name = pool.name

    request_dict = {"name": pool_ref}
    if new_display_name:
        request_dict["node_pool_id"] = node_pool_name
    if new_labels is not None:
        request_dict["labels"] = new_labels

    op = await loop.run_in_executor(
        None,
        lambda: client.update_node_pool(request_dict)
    )
    await poll_gke_operation(client, op.name, timeout=600)

    return {
        "updated": True,
        "pre_state": {"display_name": prior_display_name, "labels": prior_labels},
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
    pre_state = execution_result.get("pre_state", {})
    prior_labels = pre_state.get("labels", {})

    if not creds:
        return {"rolled_back": True, "mock": True}

    if not project_id:
        project_id = get_project_id(creds)
    client = get_container_client(creds)
    loop = asyncio.get_event_loop()
    pool_ref = f"projects/{project_id}/locations/{location}/clusters/{cluster_name}/nodePools/{node_pool_name}"

    request_dict = {"name": pool_ref, "labels": prior_labels}
    op = await loop.run_in_executor(None, lambda: client.update_node_pool(request_dict))
    await poll_gke_operation(client, op.name, timeout=600)

    return {"rolled_back": True, "node_pool_name": node_pool_name}
