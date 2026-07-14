# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

from ._client import get_container_engine_client
from ._oke_helpers import poll_work_request

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = (
    "Deleted OKE cluster cannot be reconstituted; "
    "VCN, subnets, and workloads must be reprovisioned."
)


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    cluster_id = parameters.get("cluster_id", "")
    compartment_id = parameters.get("compartment_id", "")

    if not creds:
        return {
            "action": "delete_oke_cluster",
            "cluster_id": cluster_id,
            "deleted": True,
            "mock": True,
        }

    client = get_container_engine_client(creds)
    loop = asyncio.get_running_loop()

    node_pools = await loop.run_in_executor(
        None,
        lambda: client.list_node_pools(
            compartment_id=compartment_id, cluster_id=cluster_id
        ).data,
    )
    for pool in node_pools:
        if pool.lifecycle_state not in ("DELETED", "DELETING"):
            np_resp = await loop.run_in_executor(
                None, lambda p=pool: client.delete_node_pool(p.id)
            )
            np_wr_id = np_resp.headers.get("opc-work-request-id")
            if np_wr_id:
                await poll_work_request(client, np_wr_id, "nodepool", timeout=900)

    cl_resp = await loop.run_in_executor(None, lambda: client.delete_cluster(cluster_id))
    cl_wr_id = cl_resp.headers.get("opc-work-request-id")
    if cl_wr_id:
        await poll_work_request(client, cl_wr_id, "cluster", timeout=1200)

    return {
        "action": "delete_oke_cluster",
        "cluster_id": cluster_id,
        "deleted": True,
        "deleted_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "action": "rollback_delete_oke_cluster",
        "rolled_back": False,
        "reason": ROLLBACK_REASON,
    }
