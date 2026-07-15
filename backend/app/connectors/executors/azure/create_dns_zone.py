# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    zone_name = parameters.get("zone_name", "")
    rg = parameters.get("resource_group", creds.get("resource_group", "default"))

    if not creds:
        return {"action": "create_dns_zone", "zone_name": zone_name, "resource_group": rg, "mock": True}

    from ._client import get_dns_client
    from azure.mgmt.dns.models import Zone
    dns = get_dns_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        lambda: dns.zones.create_or_update(rg, zone_name, Zone(location="global")),
    )
    return {
        "action": "create_dns_zone",
        "zone_name": zone_name,
        "resource_group": rg,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.azure.delete_dns_zone import execute as delete
    return await delete(
        {
            "zone_name": execution_result.get("zone_name", parameters.get("zone_name")),
            "resource_group": execution_result.get("resource_group", parameters.get("resource_group")),
        },
        [], connector,
    )
