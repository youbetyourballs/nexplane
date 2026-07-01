# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    policy_name = parameters["policy_name"]
    if not creds:
        return {"action": "delete_policy", "policy_name": policy_name, "deleted": True}
    from ._client import get_vault_client
    loop = asyncio.get_event_loop()
    client = get_vault_client(creds)
    await loop.run_in_executor(None, lambda: client.sys.delete_policy(name=policy_name))
    return {"action": "delete_policy", "policy_name": policy_name, "deleted": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "re-create policy with original HCL manually"}
