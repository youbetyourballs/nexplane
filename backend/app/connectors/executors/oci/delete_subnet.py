# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Delete an OCI Subnet."""
    creds = getattr(connector, "credentials", {})
    subnet_id = parameters.get("subnet_id", "")

    if not creds:
        return {"action": "delete_subnet", "subnet_id": subnet_id, "mock": True}

    if not subnet_id:
        return {"action": "delete_subnet", "skipped": True, "reason": "no subnet_id"}

    from ._client import get_network_client

    network = get_network_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: network.delete_subnet(subnet_id))
    return {
        "action": "delete_subnet",
        "subnet_id": subnet_id,
        "deleted_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "delete_subnet has no rollback"}
