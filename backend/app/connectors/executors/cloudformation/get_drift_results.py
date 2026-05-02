import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    stack_name = parameters["stack_name"]
    if not creds:
        return {"action": "get_drift_results", "stack_name": stack_name, "drift_status": "IN_SYNC", "drifted_resources": []}
    from ._client import get_client
    loop = asyncio.get_event_loop()
    cf = get_client(creds)
    result = await loop.run_in_executor(None, lambda: cf.describe_stack_resource_drifts(StackName=stack_name))
    drifted = [{"logical_id": r["LogicalResourceId"], "drift_status": r["StackResourceDriftStatus"]} for r in result.get("StackResourceDrifts", [])]
    return {"action": "get_drift_results", "stack_name": stack_name, "drifted_resources": drifted, "count": len(drifted)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "status check has no rollback"}
