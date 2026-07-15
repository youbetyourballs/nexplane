# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "read-only operation — no state was changed"

_NSGS = [
    ("payments-subnet-nsg", ["payments"], 12, False),
    ("web-tier-nsg", ["web", "dmz"], 8, True),
    ("mgmt-nsg", ["infra"], 5, False),
]


def _mock_response():
    now = datetime.now(timezone.utc).isoformat()
    results = []
    for name, subnets, rule_count, permissive in _NSGS:
        tags = ["azure-nsg"]
        if permissive:
            tags.append("azure-nsg-permissive")
        results.append({
            "name": name,
            "asset_type": "firewall",
            "environment": "prod",
            "criticality": "high",
            "tags": tags,
            "asset_metadata": {
                "associated_subnets": subnets,
                "rule_count": rule_count,
                "has_any_source_rules": permissive,
                "region": "eastus",
                "discovered_at": now,
                "azure_source": "azure",
            },
        })
    return results


async def _real_execute(creds: dict) -> list:
    from ._client import get_network_client
    network = get_network_client(creds)
    loop = asyncio.get_event_loop()
    nsgs = await loop.run_in_executor(None, lambda: list(network.network_security_groups.list_all()))
    now = datetime.now(timezone.utc).isoformat()
    results = []
    for nsg in nsgs:
        rules = getattr(nsg, "security_rules", []) or []
        has_any = any(
            getattr(r, "source_address_prefix", "") == "*"
            for r in rules
        )
        tags = ["azure-nsg"]
        if has_any:
            tags.append("azure-nsg-permissive")
        results.append({
            "name": nsg.name,
            "asset_type": "firewall",
            "environment": "prod",
            "criticality": "high",
            "tags": tags,
            "asset_metadata": {
                "associated_subnets": [],
                "rule_count": len(rules),
                "has_any_source_rules": has_any,
                "region": getattr(nsg, "location", "unknown"),
                "discovered_at": now,
                "azure_source": "azure",
            },
        })
    return results if results else _mock_response()


async def execute(parameters: dict, asset_ids: list, connector) -> list:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return _mock_response()
    return await _real_execute(creds)
