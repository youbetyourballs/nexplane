# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "NSG deletion is destructive; security rules and VNIC associations are permanently lost"

import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    nsg_id = parameters.get("nsg_id", "")

    if not creds:
        return {
            "action": "delete_nsg",
            "nsg_id": nsg_id or "ocid1.networksecuritygroup.mock",
            "deleted": True,
            "mock": True,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_network_client
    loop = asyncio.get_running_loop()
    network = get_network_client(creds)

    await loop.run_in_executor(
        None, lambda: network.delete_network_security_group(nsg_id)
    )

    return {
        "action": "delete_nsg",
        "nsg_id": nsg_id,
        "deleted": True,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "delete_nsg is destructive; security rules and VNIC associations are permanently lost"}
