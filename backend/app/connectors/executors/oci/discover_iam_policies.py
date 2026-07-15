# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from ._client import get_identity_client

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "read-only operation — no state was changed"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"assets": [], "mock": True}
    client = get_identity_client(creds)
    compartment_id = creds.get("compartment_id") or creds.get("tenancy_id") or creds.get("tenancy", "")
    loop = asyncio.get_running_loop()
    import oci.pagination
    policies = await loop.run_in_executor(
        None,
        lambda: oci.pagination.list_call_get_all_results(
            client.list_policies, compartment_id=compartment_id
        ).data,
    )
    assets = []
    for p in policies:
        if p.lifecycle_state == "DELETED":
            continue
        assets.append({
            "name": p.name,
            "asset_type": "application",
            "environment": "prod",
            "criticality": "high",
            "asset_metadata": {
                "policy_id": p.id,
                "name": p.name,
                "statements": list(p.statements),
                "compartment_id": p.compartment_id,
                "provider": "oci",
            },
            "tags": ["oci", "oci-iam-policy"],
            "_dedup_key": p.id,
        })
    return {"assets": assets, "count": len(assets)}
