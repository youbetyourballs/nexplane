import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_secrets", "secrets": [{"name": "mock-secret", "namespace": "default", "type": "Opaque"}], "count": 1}
    from ._client import get_k8s_clients
    loop = asyncio.get_event_loop()
    clients = get_k8s_clients(creds)
    secret_list = await loop.run_in_executor(None, lambda: clients["core"].list_secret_for_all_namespaces())
    secrets = [{"name": s.metadata.name, "namespace": s.metadata.namespace, "type": s.type} for s in secret_list.items]
    return {"action": "discover_secrets", "secrets": secrets, "count": len(secrets)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
