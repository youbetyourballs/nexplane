# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    resource_group = parameters["resource_group"]
    deployment_name = parameters["deployment_name"]
    if not creds:
        return {"action": "delete_deployment", "deployment_name": deployment_name, "deleted": True}

    from ._client import arm_delete
    sub = creds["subscription_id"]
    path = f"/subscriptions/{sub}/resourcegroups/{resource_group}/providers/Microsoft.Resources/deployments/{deployment_name}"
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: arm_delete(creds, path))
    return {"action": "delete_deployment", "deployment_name": deployment_name, "deleted": True}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "deployment record deletion is irreversible"}
