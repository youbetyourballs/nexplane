import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_email = parameters["user_email"]
    group_email = parameters["group_email"]
    if not creds:
        return {"action": "remove_from_group", "user_email": user_email, "group_email": group_email, "removed": True}
    from ._client import get_admin_service
    loop = asyncio.get_event_loop()
    service = get_admin_service(creds, "admin", "directory_v1")
    await loop.run_in_executor(None, lambda: service.members().delete(groupKey=group_email, memberKey=user_email).execute())
    return {"action": "remove_from_group", "user_email": user_email, "group_email": group_email, "removed": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "re-add user to group manually"}
