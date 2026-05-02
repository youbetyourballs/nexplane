import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_rbac", "cluster_roles": [], "role_bindings": [], "count": 0}
    from ._client import get_k8s_clients
    loop = asyncio.get_event_loop()
    clients = get_k8s_clients(creds)
    cr_list = await loop.run_in_executor(None, lambda: clients["rbac"].list_cluster_role())
    rb_list = await loop.run_in_executor(None, lambda: clients["rbac"].list_cluster_role_binding())
    cluster_roles = [{"name": cr.metadata.name} for cr in cr_list.items]
    role_bindings = [{"name": rb.metadata.name, "subjects": len(rb.subjects or [])} for rb in rb_list.items]
    return {"action": "discover_rbac", "cluster_roles": cluster_roles, "role_bindings": role_bindings, "count": len(cluster_roles)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
