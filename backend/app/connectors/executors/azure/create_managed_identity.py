# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    name = parameters.get("identity_name", "")
    rg = parameters.get("resource_group", creds.get("resource_group", "default"))
    location = parameters.get("location", "eastus")

    if not creds:
        return {
            "action": "create_managed_identity",
            "identity_name": name,
            "resource_group": rg,
            "principal_id": "mock-principal-id",
            "client_id": "mock-client-id",
            "mock": True,
        }

    from ._client import get_msi_client
    msi = get_msi_client(creds)
    loop = asyncio.get_running_loop()
    identity = await loop.run_in_executor(
        None,
        lambda: msi.user_assigned_identities.create_or_update(rg, name, {"location": location}),
    )
    return {
        "action": "create_managed_identity",
        "identity_name": name,
        "resource_group": rg,
        "principal_id": str(identity.principal_id),
        "client_id": str(identity.client_id),
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.azure.delete_managed_identity import execute as delete
    return await delete(
        {
            "identity_name": execution_result.get("identity_name", parameters.get("identity_name")),
            "resource_group": execution_result.get("resource_group", parameters.get("resource_group")),
        },
        [], connector,
    )
