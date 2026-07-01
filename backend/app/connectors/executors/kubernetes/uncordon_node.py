# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    node_name = parameters["node_name"]
    if not creds:
        return {"action": "uncordon_node", "node_name": node_name, "unschedulable": False}
    from ._client import get_k8s_clients
    loop = asyncio.get_event_loop()
    clients = get_k8s_clients(creds)
    patch = {"spec": {"unschedulable": False}}
    await loop.run_in_executor(None, lambda: clients["core"].patch_node(node_name, patch))
    return {"action": "uncordon_node", "node_name": node_name, "unschedulable": False}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "uncordon rollback would cordon — use cordon_node explicitly"}
