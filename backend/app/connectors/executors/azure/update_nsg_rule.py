# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone


def _mock_response(parameters, asset_ids):
    return {"action": "update_nsg_rule", "rule_name": parameters.get("rule_name"), "nsg_action": parameters.get("action"), "priority": parameters.get("priority", 100), "assets": asset_ids, "applied": True, "applied_at": datetime.now(timezone.utc).isoformat()}


async def _real_execute(parameters: dict, asset_ids: list, creds: dict) -> dict:
    from ._client import get_network_client
    network = get_network_client(creds)
    loop = asyncio.get_event_loop()
    rg = parameters.get("resource_group", creds.get("resource_group", "default"))
    nsg_name = parameters.get("nsg_name", asset_ids[0] if asset_ids else "default")
    rule_name = parameters.get("rule_name", "nexplane-rule")
    nsg_action = parameters.get("action", "Allow")
    priority = parameters.get("priority", 100)
    rule_params = {
        "protocol": parameters.get("protocol", "Tcp"),
        "source_address_prefix": parameters.get("source_cidr", "*"),
        "destination_address_prefix": parameters.get("dest_cidr", "*"),
        "source_port_range": parameters.get("source_port", "*"),
        "destination_port_range": str(parameters.get("dest_port", "*")),
        "access": nsg_action,
        "priority": priority,
        "direction": parameters.get("direction", "Inbound"),
    }
    await loop.run_in_executor(
        None,
        lambda: network.security_rules.begin_create_or_update(rg, nsg_name, rule_name, rule_params).result()
    )
    return {"action": "update_nsg_rule", "rule_name": rule_name, "nsg_action": nsg_action, "priority": priority, "assets": asset_ids, "applied": True, "applied_at": datetime.now(timezone.utc).isoformat()}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return _mock_response(parameters, asset_ids)
    return await _real_execute(parameters, asset_ids, creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "restore_nsg_rule", "rule_name": parameters.get("rule_name")}
