import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    namespace = parameters["namespace"]
    policy_name = parameters["policy_name"]
    if not creds:
        return {"action": "delete_network_policy", "namespace": namespace, "policy_name": policy_name, "deleted": True}
    from ._client import get_k8s_clients
    loop = asyncio.get_event_loop()
    clients = get_k8s_clients(creds)
    await loop.run_in_executor(None, lambda: clients["networking"].delete_namespaced_network_policy(policy_name, namespace))
    return {"action": "delete_network_policy", "namespace": namespace, "policy_name": policy_name, "deleted": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "re-create network policy manually"}
