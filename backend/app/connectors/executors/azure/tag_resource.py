from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    resource_id = parameters['resource_id']
    tags = parameters['tags']
    if not creds:
        return {"action": "tag_resource", "resource_id": resource_id, "tags": tags, "mock": True}
    import asyncio
    from azure.mgmt.resource import ResourceManagementClient
    from azure.identity import ClientSecretCredential
    credential = ClientSecretCredential(creds['tenant_id'], creds['client_id'], creds['client_secret'])
    rc = ResourceManagementClient(credential, creds['subscription_id'])
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: rc.tags.update_at_scope(resource_id, {"operation": "Merge", "properties": {"tags": tags}}))
    return {"action": "tag_resource", "resource_id": resource_id, "tags": tags, "executed_at": datetime.now(timezone.utc).isoformat()}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "tag changes have no automatic rollback"}
