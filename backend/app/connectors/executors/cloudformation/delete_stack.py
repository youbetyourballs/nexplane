# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    stack_name = parameters["stack_name"]
    if not creds:
        return {"action": "delete_stack", "stack_name": stack_name, "deleted": True}
    from ._client import get_client
    loop = asyncio.get_event_loop()
    cf = get_client(creds)
    await loop.run_in_executor(None, lambda: cf.delete_stack(StackName=stack_name))
    return {"action": "delete_stack", "stack_name": stack_name, "deleted": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "stack deletion is irreversible"}
