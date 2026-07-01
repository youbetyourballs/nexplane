# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone
from ._client import get_identity_client


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters.get("user_id", "")
    name = parameters.get("name", user_id)

    if not creds:
        return {"action": "delete_iam_user", "user_id": user_id, "mock": True}

    client = get_identity_client(creds)
    tenancy_id = creds.get("tenancy_id") or creds.get("tenancy", "")
    loop = asyncio.get_running_loop()

    def _call():
        import oci.pagination
        # Remove user from all groups first
        memberships = oci.pagination.list_call_get_all_results(
            client.list_user_group_memberships,
            compartment_id=tenancy_id,
            user_id=user_id,
        ).data
        for m in memberships:
            client.remove_user_from_group(m.id)
        client.delete_user(user_id)

    await loop.run_in_executor(None, _call)
    return {
        "action": "delete_iam_user",
        "user_id": user_id,
        "name": name,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "delete_iam_user is destructive; no rollback available"}
