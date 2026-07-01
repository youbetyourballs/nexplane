# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

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
    from azure.mgmt.authorization.models import RoleAssignmentCreateParameters
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

    import time as _time
    assignment_id = str(uuid.uuid4())
    principal_type = parameters.get("principal_type", "ServicePrincipal")
    # Retry to handle AAD replication delay after principal creation
    last_exc = None
    for attempt in range(6):
        try:
            await loop.run_in_executor(
                None,
                lambda: auth.role_assignments.create(
                    scope, assignment_id,
                    RoleAssignmentCreateParameters(
                        role_definition_id=role_definition_id,
                        principal_id=principal_id,
                        principal_type=principal_type,
                    ),
                ),
            )
            break
        except Exception as exc:
            if "PrincipalNotFound" in str(exc) and attempt < 5:
                await asyncio.sleep(15)
                last_exc = exc
            else:
                raise
    else:
        raise last_exc
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
