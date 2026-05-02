import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    stack_name = parameters["stack_name"]
    change_set_name = parameters["change_set_name"]
    if not creds:
        return {"action": "execute_change_set", "stack_name": stack_name, "change_set_name": change_set_name, "executed": True}
    from ._client import get_client
    loop = asyncio.get_event_loop()
    cf = get_client(creds)
    await loop.run_in_executor(None, lambda: cf.execute_change_set(StackName=stack_name, ChangeSetName=change_set_name))
    return {"action": "execute_change_set", "stack_name": stack_name, "change_set_name": change_set_name, "executed": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "stack update rollback requires creating a new change set to revert"}
