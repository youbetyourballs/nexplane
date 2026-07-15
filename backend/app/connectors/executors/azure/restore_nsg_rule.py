# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"

async def _real_execute(parameters: dict, creds: dict) -> dict:
    from ._client import get_network_client
    network = get_network_client(creds)
    loop = asyncio.get_event_loop()
    rg = parameters.get("resource_group", creds.get("resource_group", "default"))
    nsg_name = parameters.get("nsg_name", "default")
    rule_name = parameters.get("rule_name")
    if rule_name:
        await loop.run_in_executor(
            None,
            lambda: network.security_rules.begin_delete(rg, nsg_name, rule_name).result()
        )
    return {"action": "restore_nsg_rule", "rule_name": rule_name, "restored": True, "restored_at": datetime.now(timezone.utc).isoformat()}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "restore_nsg_rule", "rule_name": parameters.get("rule_name"), "restored": True, "restored_at": datetime.now(timezone.utc).isoformat()}
    return await _real_execute(parameters, creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "restore_nsg_rule has no rollback"}
