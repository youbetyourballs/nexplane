import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_email = parameters["user_email"]

    if not creds:
        return {"action": "reset_2fa", "user_email": user_email, "reset": True, "mock": True}

    from ._client import get_admin_service
    loop = asyncio.get_event_loop()
    service = get_admin_service(creds, "admin", "directory_v1")

    def _call():
        service.twoStepVerification().turnOff(userKey=user_email).execute()

    await loop.run_in_executor(None, _call)
    return {
        "action": "reset_2fa",
        "user_email": user_email,
        "reset": True,
        "rollback_data": None,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": False,
        "reason": "2FA reset cannot be automatically rolled back. User must re-enroll.",
    }
