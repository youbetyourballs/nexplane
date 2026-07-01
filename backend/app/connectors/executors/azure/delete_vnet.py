# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    vnet_name = parameters.get("vnet_name", "")
    rg = parameters.get("resource_group", creds.get("resource_group", "default"))

    if not creds:
        return {"action": "delete_vnet", "vnet_name": vnet_name, "resource_group": rg, "mock": True}

    from ._client import get_network_client
    network = get_network_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: network.virtual_networks.begin_delete(rg, vnet_name).result())
    return {
        "action": "delete_vnet",
        "vnet_name": vnet_name,
        "resource_group": rg,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "VNet deletion cannot be reversed automatically"}
