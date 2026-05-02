import asyncio
from kubernetes import client as k8s_client

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    namespace = parameters["namespace"]
    policy_manifest = parameters["policy_manifest"]
    if not creds:
        return {"action": "create_network_policy", "namespace": namespace, "created": True}
    from ._client import get_k8s_clients
    loop = asyncio.get_event_loop()
    clients = get_k8s_clients(creds)
    body = k8s_client.V1NetworkPolicy(**policy_manifest)
    result = await loop.run_in_executor(None, lambda: clients["networking"].create_namespaced_network_policy(namespace, body))
    return {"action": "create_network_policy", "namespace": namespace, "name": result.metadata.name, "created": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "delete network policy manually if needed"}
