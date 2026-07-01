# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    name = parameters.get("storage_account_name", "")
    rg = parameters.get("resource_group", creds.get("resource_group", "default"))
    location = parameters.get("location", "eastus")
    sku = parameters.get("sku", "Standard_LRS")
    kind = parameters.get("kind", "StorageV2")

    if not creds:
        return {
            "action": "create_storage_account",
            "storage_account_name": name,
            "resource_group": rg,
            "location": location,
            "mock": True,
        }

    from ._client import get_storage_client
    from azure.mgmt.storage.models import StorageAccountCreateParameters, Sku, Kind
    storage = get_storage_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        lambda: storage.storage_accounts.begin_create(
            rg, name,
            StorageAccountCreateParameters(
                sku=Sku(name=sku),
                kind=Kind(kind),
                location=location,
            ),
        ).result(),
    )
    return {
        "action": "create_storage_account",
        "storage_account_name": name,
        "resource_group": rg,
        "location": location,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.azure.delete_storage_account import execute as delete
    return await delete(
        {
            "storage_account_name": execution_result.get("storage_account_name", parameters.get("storage_account_name")),
            "resource_group": execution_result.get("resource_group", parameters.get("resource_group")),
        },
        [], connector,
    )
