import asyncio
import uuid
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    principal_id = parameters.get("principal_id", "")
    role_name = parameters.get("role_definition_name", "Reader")
    scope = parameters.get("scope", f"/subscriptions/{creds.get('subscription_id', 'mock')}")

    if not creds:
        return {
            "action": "assign_role",
            "assignment_id": "mock-assignment-id",
            "principal_id": principal_id,
            "role_definition_name": role_name,
            "scope": scope,
            "mock": True,
        }

    from ._client import get_authorization_client
    auth = get_authorization_client(creds)
    loop = asyncio.get_running_loop()

    # Look up built-in role definition ID by name
    roles = await loop.run_in_executor(
        None,
        lambda: list(auth.role_definitions.list(scope, filter=f"roleName eq '{role_name}'")),
    )
    if not roles:
        raise ValueError(f"Role '{role_name}' not found at scope '{scope}'")
    role_definition_id = roles[0].id

    assignment_id = str(uuid.uuid4())
    await loop.run_in_executor(
        None,
        lambda: auth.role_assignments.create(
            scope, assignment_id,
            {"role_definition_id": role_definition_id, "principal_id": principal_id},
        ),
    )
    return {
        "action": "assign_role",
        "assignment_id": assignment_id,
        "principal_id": principal_id,
        "role_definition_name": role_name,
        "scope": scope,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.azure.remove_role_assignment import execute as remove
    return await remove(
        {
            "assignment_id": execution_result.get("assignment_id"),
            "scope": execution_result.get("scope", parameters.get("scope")),
        },
        [], connector,
    )
