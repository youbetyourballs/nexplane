import asyncio
from kubernetes import client as k8s_client

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    node_name = parameters["node_name"]
    if not creds:
        return {"action": "cordon_node", "node_name": node_name, "unschedulable": True}
    from ._client import get_k8s_clients
    loop = asyncio.get_event_loop()
    clients = get_k8s_clients(creds)
    patch = {"spec": {"unschedulable": True}}
    await loop.run_in_executor(None, lambda: clients["core"].patch_node(node_name, patch))
    return {"action": "cordon_node", "node_name": node_name, "unschedulable": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.kubernetes.uncordon_node import execute as uncordon
    return await uncordon(parameters, [], connector)
