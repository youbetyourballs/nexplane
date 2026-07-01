# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    project = parameters["project"]
    stack = parameters["stack"]
    if not creds:
        return {"action": "cancel_update", "project": project, "stack": stack, "cancelled": True}
    from ._client import get_client
    org = creds["organization"]
    async with get_client(creds) as client:
        resp = await client.post(f"/stacks/{org}/{project}/{stack}/cancel")
        resp.raise_for_status()
    return {"action": "cancel_update", "project": project, "stack": stack, "cancelled": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "cancel has no rollback"}
