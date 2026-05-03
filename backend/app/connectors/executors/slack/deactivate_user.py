import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters["user_id"]

    if not creds:
        return {
            "action": "deactivate_user",
            "user_id": user_id,
            "deactivated": True,
            "rollback_data": {"user_id": user_id, "was_active": True},
            "mock": True,
        }

    from ._client import get_slack_client
    loop = asyncio.get_event_loop()
    client = get_slack_client(creds, token_type="admin")

    def _call():
        resp = client.admin_users_setInactive(user_id=user_id)
        if not resp["ok"]:
            raise RuntimeError(f"Slack API error: {resp.get('error')}")

    await loop.run_in_executor(None, _call)
    return {
        "action": "deactivate_user",
        "user_id": user_id,
        "deactivated": True,
        "rollback_data": {"user_id": user_id, "was_active": True},
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.slack.reactivate_user import execute as reactivate
    return await reactivate(parameters, [], connector)
