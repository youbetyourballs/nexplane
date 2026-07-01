# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from ._client import get_identity_client


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"assets": [], "mock": True}
    client = get_identity_client(creds)
    tenancy_id = creds.get("tenancy_id") or creds.get("tenancy", "")
    loop = asyncio.get_running_loop()
    import oci.pagination
    users = await loop.run_in_executor(
        None,
        lambda: oci.pagination.list_call_get_all_results(
            client.list_users, compartment_id=tenancy_id
        ).data,
    )
    assets = []
    for u in users:
        if u.lifecycle_state == "DELETED":
            continue
        caps = u.capabilities
        assets.append({
            "name": u.name,
            "asset_type": "identity",
            "environment": "prod",
            "criticality": "high",
            "asset_metadata": {
                "user_id": u.id,
                "email": u.email or "",
                "lifecycle_state": u.lifecycle_state,
                "is_mfa_activated": u.is_mfa_activated,
                "can_use_console_password": caps.can_use_console_password if caps else True,
                "can_use_api_keys": caps.can_use_api_keys if caps else True,
                "provider": "oci",
            },
            "tags": ["oci", "iam-user"],
            "_dedup_key": u.id,
        })
    return {"assets": assets, "count": len(assets)}
