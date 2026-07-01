# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    project = parameters["project"]
    stack = parameters["stack"]
    if not creds:
        return {"action": "discover_stack_resources", "resources": [], "count": 0}
    from ._client import get_client
    org = creds["organization"]
    async with get_client(creds) as client:
        resp = await client.get(f"/stacks/{org}/{project}/{stack}/export")
        resp.raise_for_status()
        resources = resp.json().get("deployment", {}).get("resources", [])
    return {"action": "discover_stack_resources", "resources": resources, "count": len(resources)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
