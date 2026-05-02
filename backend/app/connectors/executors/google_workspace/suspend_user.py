import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_email = parameters["user_email"]
    if not creds:
        return {"action": "suspend_user", "user_email": user_email, "suspended": True}
    from ._client import get_admin_service
    loop = asyncio.get_event_loop()
    service = get_admin_service(creds, "admin", "directory_v1")
    await loop.run_in_executor(None, lambda: service.users().update(userKey=user_email, body={"suspended": True}).execute())
    return {"action": "suspend_user", "user_email": user_email, "suspended": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.google_workspace.unsuspend_user import execute as unsuspend
    return await unsuspend(parameters, [], connector)
