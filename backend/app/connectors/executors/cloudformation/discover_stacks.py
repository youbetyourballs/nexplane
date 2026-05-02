import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_stacks", "stacks": [{"name": "mock-stack", "status": "CREATE_COMPLETE"}], "count": 1}
    from ._client import get_client
    loop = asyncio.get_event_loop()
    cf = get_client(creds)
    paginator = cf.get_paginator("list_stacks")
    stacks = []
    pages = await loop.run_in_executor(None, lambda: list(paginator.paginate(StackStatusFilter=["CREATE_COMPLETE", "UPDATE_COMPLETE", "ROLLBACK_COMPLETE"])))
    for page in pages:
        for s in page.get("StackSummaries", []):
            stacks.append({"name": s["StackName"], "status": s["StackStatus"], "created": str(s.get("CreationTime", ""))})
    return {"action": "discover_stacks", "stacks": stacks, "count": len(stacks)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
