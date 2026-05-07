from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    tags = parameters['tags']
    resource_id = parameters.get('resource_id', '')
    if not creds:
        return {"action": "tag_resource", "resource_id": resource_id, "tags": tags, "mock": True}
    import asyncio
    from azure.mgmt.resource import ResourceManagementClient
    from azure.mgmt.compute import ComputeManagementClient
    from azure.identity import ClientSecretCredential
    credential = ClientSecretCredential(creds['tenant_id'], creds['client_id'], creds['client_secret'])
    subscription_id = creds['subscription_id']
    # Build resource_id if not provided
    if not resource_id:
        resource_group = parameters.get('resource_group', '')
        resource_name = parameters.get('resource_name', parameters.get('vm_name', ''))
        resource_type = parameters.get('resource_type', 'virtualMachines')
        resource_id = (
            f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
            f"/providers/Microsoft.Compute/{resource_type}/{resource_name}"
        )
    rc = ResourceManagementClient(credential, subscription_id)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: rc.tags.begin_update_at_scope(resource_id, {"operation": "Merge", "properties": {"tags": tags}}).result())
    return {"action": "tag_resource", "resource_id": resource_id, "tags": tags, "executed_at": datetime.now(timezone.utc).isoformat()}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "tag changes have no automatic rollback"}
