import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    stack_name = parameters["stack_name"]
    if not creds:
        return {"action": "discover_stack_resources", "resources": [{"logical_id": "MyBucket", "physical_id": "my-bucket-xyz", "type": "AWS::S3::Bucket"}], "count": 1}
    from ._client import get_client
    loop = asyncio.get_event_loop()
    cf = get_client(creds)
    result = await loop.run_in_executor(None, lambda: cf.list_stack_resources(StackName=stack_name))
    resources = [{"logical_id": r["LogicalResourceId"], "physical_id": r.get("PhysicalResourceId"), "type": r["ResourceType"], "status": r["ResourceStatus"]} for r in result.get("StackResourceSummaries", [])]
    return {"action": "discover_stack_resources", "resources": resources, "count": len(resources)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
