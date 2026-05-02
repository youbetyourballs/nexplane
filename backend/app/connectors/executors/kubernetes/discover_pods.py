import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_pods", "pods": [{"name": "mock-pod-1", "namespace": "default", "status": "Running", "node": "mock-node-1"}], "count": 1}
    from ._client import get_k8s_clients
    loop = asyncio.get_event_loop()
    clients = get_k8s_clients(creds)
    ns = creds.get("namespace")
    if ns:
        pod_list = await loop.run_in_executor(None, lambda: clients["core"].list_namespaced_pod(ns))
    else:
        pod_list = await loop.run_in_executor(None, lambda: clients["core"].list_pod_for_all_namespaces())
    pods = [{"name": p.metadata.name, "namespace": p.metadata.namespace, "status": p.status.phase, "node": p.spec.node_name} for p in pod_list.items]
    return {"action": "discover_pods", "pods": pods, "count": len(pods)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
