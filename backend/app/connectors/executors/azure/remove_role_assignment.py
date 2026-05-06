import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    assignment_id = parameters.get("assignment_id", "")
    scope = parameters.get("scope", f"/subscriptions/{creds.get('subscription_id', 'mock')}")

    if not creds:
        return {"action": "remove_role_assignment", "assignment_id": assignment_id, "scope": scope, "mock": True}

    from ._client import get_authorization_client
    auth = get_authorization_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: auth.role_assignments.delete(scope, assignment_id))
    return {
        "action": "remove_role_assignment",
        "assignment_id": assignment_id,
        "scope": scope,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "role assignment deletion cannot be reversed automatically"}
