# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    account = parameters.get("storage_account_name", "")
    container = parameters.get("container_name", "")
    rg = parameters.get("resource_group", creds.get("resource_group", "default"))

    if not creds:
        return {
            "action": "delete_blob_container",
            "storage_account_name": account,
            "container_name": container,
            "resource_group": rg,
            "mock": True,
        }

    from ._client import get_storage_client
    storage = get_storage_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: storage.blob_containers.delete(rg, account, container))
    return {
        "action": "delete_blob_container",
        "storage_account_name": account,
        "container_name": container,
        "resource_group": rg,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "blob container deletion cannot be reversed automatically"}
