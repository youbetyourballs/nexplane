# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "read-only operation — no state was changed"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_nodes", "nodes": [{"name": "mock-node-1", "status": "Ready", "cpu": "4", "memory": "16Gi"}], "count": 1}
    from ._client import get_k8s_clients
    loop = asyncio.get_event_loop()
    clients = get_k8s_clients(creds)
    nodes_list = await loop.run_in_executor(None, lambda: clients["core"].list_node())
    nodes = [{"name": n.metadata.name, "status": n.status.conditions[-1].type if n.status.conditions else "Unknown", "cpu": n.status.capacity.get("cpu"), "memory": n.status.capacity.get("memory")} for n in nodes_list.items]
    return {"action": "discover_nodes", "nodes": nodes, "count": len(nodes)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
