# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    zone_name = parameters.get("zone_name", "")
    rg = parameters.get("resource_group", creds.get("resource_group", "default"))

    if not creds:
        return {"action": "delete_dns_zone", "zone_name": zone_name, "resource_group": rg, "mock": True}

    from ._client import get_dns_client
    dns = get_dns_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: dns.zones.begin_delete(rg, zone_name).result())
    return {
        "action": "delete_dns_zone",
        "zone_name": zone_name,
        "resource_group": rg,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "DNS zone deletion cannot be reversed automatically"}
