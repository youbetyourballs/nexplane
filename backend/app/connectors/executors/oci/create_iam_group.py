# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone
from ._client import get_identity_client


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    name = parameters.get("name", "nexplane-group")
    description = parameters.get("description", "Created by Nexplane")
    user_ids = parameters.get("user_ids", [])

    auto_asset = {
        "name": name,
        "asset_type": "application",
        "environment": "prod",
        "criticality": "medium",
        "asset_metadata": {"name": name, "provider": "oci"},
        "tags": ["oci", "oci-iam-group", "nexplane-managed"],
    }

    if not creds:
        return {
            "action": "create_iam_group",
            "name": name,
            "group_id": "ocid1.group.oc1..mock",
            "mock": True,
            "_auto_asset": auto_asset,
        }

    client = get_identity_client(creds)
    tenancy_id = creds.get("tenancy_id") or creds.get("tenancy", "")
    loop = asyncio.get_running_loop()

    def _call():
        import oci
        details = oci.identity.models.CreateGroupDetails(
            compartment_id=tenancy_id,
            name=name,
            description=description,
        )
        group = client.create_group(details).data
        for uid in user_ids:
            membership = oci.identity.models.AddUserToGroupDetails(
                user_id=uid, group_id=group.id
            )
            client.add_user_to_group(membership)
        return group

    group = await loop.run_in_executor(None, _call)
    auto_asset["asset_metadata"]["group_id"] = group.id
    return {
        "action": "create_iam_group",
        "name": name,
        "group_id": group.id,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "_auto_asset": auto_asset,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.oci.delete_iam_group import execute as delete
    return await delete(
        {"group_id": execution_result.get("group_id"), "name": execution_result.get("name")},
        [],
        connector,
    )
