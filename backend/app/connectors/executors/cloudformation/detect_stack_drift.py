import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    stack_name = parameters["stack_name"]
    if not creds:
        return {"action": "detect_stack_drift", "stack_name": stack_name, "drift_detection_id": "mock-drift-id"}
    from ._client import get_client
    loop = asyncio.get_event_loop()
    cf = get_client(creds)
    result = await loop.run_in_executor(None, lambda: cf.detect_stack_drift(StackName=stack_name))
    return {"action": "detect_stack_drift", "stack_name": stack_name, "drift_detection_id": result["StackDriftDetectionId"]}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "drift detection has no rollback"}
