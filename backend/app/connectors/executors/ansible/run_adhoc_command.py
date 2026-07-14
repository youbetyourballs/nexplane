# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "ad-hoc command has already executed — output cannot be reversed"

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    inventory_id = parameters["inventory_id"]
    module_name = parameters["module_name"]
    if not creds:
        return {"action": "run_adhoc_command", "inventory_id": inventory_id, "module": module_name, "job_id": "mock-adhoc-1"}
    from ._client import get_client
    body = {"inventory": inventory_id, "module_name": module_name, "module_args": parameters.get("module_args", ""), "limit": parameters.get("limit", "")}
    async with get_client(creds) as client:
        resp = await client.post("/ad_hoc_commands/", json=body)
        resp.raise_for_status()
        cmd = resp.json()
    return {"action": "run_adhoc_command", "inventory_id": inventory_id, "module": module_name, "job_id": cmd["id"]}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "ad-hoc command cannot be automatically reversed"}
