# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    node_name = parameters["node_name"]
    if not creds:
        return {"action": "drain_node", "node_name": node_name, "drained": True}
    from ._client import get_k8s_clients
    loop = asyncio.get_event_loop()
    clients = get_k8s_clients(creds)
    # Cordon node first
    patch = {"spec": {"unschedulable": True}}
    await loop.run_in_executor(None, lambda: clients["core"].patch_node(node_name, patch))
    # Evict pods on the node
    pod_list = await loop.run_in_executor(None, lambda: clients["core"].list_pod_for_all_namespaces(field_selector=f"spec.nodeName={node_name}"))
    evicted = []
    for pod in pod_list.items:
        try:
            await loop.run_in_executor(None, lambda p=pod: clients["core"].delete_namespaced_pod(p.metadata.name, p.metadata.namespace))
            evicted.append(pod.metadata.name)
        except Exception:
            pass
    return {"action": "drain_node", "node_name": node_name, "drained": True, "evicted_pods": evicted}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.kubernetes.uncordon_node import execute as uncordon
    return await uncordon(parameters, [], connector)
