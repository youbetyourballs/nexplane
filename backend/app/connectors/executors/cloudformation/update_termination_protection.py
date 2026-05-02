import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    stack_name = parameters["stack_name"]
    enable = parameters["enable"]
    if not creds:
        return {"action": "update_termination_protection", "stack_name": stack_name, "enabled": enable}
    from ._client import get_client
    loop = asyncio.get_event_loop()
    cf = get_client(creds)
    await loop.run_in_executor(None, lambda: cf.update_termination_protection(StackName=stack_name, EnableTerminationProtection=enable))
    return {"action": "update_termination_protection", "stack_name": stack_name, "enabled": enable}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "revert termination protection manually"}
