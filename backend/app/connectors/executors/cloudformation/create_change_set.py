import asyncio
import uuid

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    stack_name = parameters["stack_name"]
    change_set_type = parameters.get("change_set_type", "UPDATE")
    if not creds:
        return {"action": "create_change_set", "stack_name": stack_name, "change_set_name": "mock-changeset-1"}
    from ._client import get_client
    loop = asyncio.get_event_loop()
    cf = get_client(creds)
    change_set_name = f"nexplane-{uuid.uuid4().hex[:8]}"
    kwargs = {
        "StackName": stack_name,
        "ChangeSetName": change_set_name,
        "ChangeSetType": change_set_type,
    }
    if parameters.get("template_body"):
        kwargs["TemplateBody"] = parameters["template_body"]
    elif parameters.get("template_url"):
        kwargs["TemplateURL"] = parameters["template_url"]
    await loop.run_in_executor(None, lambda: cf.create_change_set(**kwargs))
    return {"action": "create_change_set", "stack_name": stack_name, "change_set_name": change_set_name}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "delete change set manually if needed"}
