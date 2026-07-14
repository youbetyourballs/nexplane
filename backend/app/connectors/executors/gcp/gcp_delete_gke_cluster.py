# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "Deleted GKE cluster cannot be reconstituted; VPC, subnets, and workloads must be reprovisioned."


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    cluster_name = parameters["cluster_name"]
    location = parameters["location"]

    if not creds:
        return {"deleted": True, "cluster_name": cluster_name, "location": location, "mock": True}

    from ._client import get_container_client, get_project_id
    from ._gke_helpers import poll_gke_operation

    project_id = get_project_id(creds)
    client = get_container_client(creds)
    loop = asyncio.get_event_loop()
    cluster_ref = f"projects/{project_id}/locations/{location}/clusters/{cluster_name}"

    op = await loop.run_in_executor(None, lambda: client.delete_cluster({"name": cluster_ref}))
    await poll_gke_operation(client, op.name, timeout=1200)

    return {"deleted": True, "cluster_name": cluster_name, "location": location, "project_id": project_id}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": ROLLBACK_REASON}
