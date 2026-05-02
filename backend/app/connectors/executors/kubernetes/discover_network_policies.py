import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_network_policies", "policies": [], "count": 0}
    from ._client import get_k8s_clients
    loop = asyncio.get_event_loop()
    clients = get_k8s_clients(creds)
    np_list = await loop.run_in_executor(None, lambda: clients["networking"].list_network_policy_for_all_namespaces())
    policies = [{"name": np.metadata.name, "namespace": np.metadata.namespace} for np in np_list.items]
    return {"action": "discover_network_policies", "policies": policies, "count": len(policies)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
