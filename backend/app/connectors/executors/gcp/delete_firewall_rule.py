# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    rule_name = parameters["rule_name"]
    if not creds:
        return {"action": "delete_firewall_rule", "rule_name": rule_name, "deleted": True}
    from ._client import get_credentials, get_project_id
    from google.cloud import compute_v1
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()
    client = compute_v1.FirewallsClient(credentials=credentials)
    op = await loop.run_in_executor(None, lambda: client.delete(project=project, firewall=rule_name))
    return {"action": "delete_firewall_rule", "rule_name": rule_name, "operation": op.name}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "firewall rule deletion rollback requires recreating the rule manually"}
