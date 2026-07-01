# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from ._client import get_vault_client


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"assets": [], "mock": True}
    compartment_id = creds.get("compartment_id") or creds.get("tenancy_id") or creds.get("tenancy", "")
    loop = asyncio.get_running_loop()
    vault_client = get_vault_client(creds)
    import oci.pagination
    # find an active vault
    vaults_list = await loop.run_in_executor(
        None,
        lambda: oci.pagination.list_call_get_all_results(
            vault_client.list_vaults, compartment_id=compartment_id
        ).data,
    )
    active_vaults = [v for v in vaults_list if v.lifecycle_state == "ACTIVE"]
    if not active_vaults:
        return {
            "assets": [],
            "count": 0,
            "warning": "No ACTIVE Vault found in compartment; skipping secret discovery.",
        }
    secrets = await loop.run_in_executor(
        None,
        lambda: oci.pagination.list_call_get_all_results(
            vault_client.list_secrets, compartment_id=compartment_id
        ).data,
    )
    assets = []
    for s in secrets:
        if s.lifecycle_state in ("DELETED", "SCHEDULED_DELETION", "PENDING_DELETION"):
            continue
        assets.append({
            "name": s.secret_name,
            "asset_type": "application",
            "environment": "prod",
            "criticality": "critical",
            "asset_metadata": {
                "secret_id": s.id,
                "secret_name": s.secret_name,
                "vault_id": s.vault_id,
                "lifecycle_state": s.lifecycle_state,
                "provider": "oci",
            },
            "tags": ["oci", "oci-vault-secret"],
            "_dedup_key": s.id,
        })
    return {"assets": assets, "count": len(assets)}
