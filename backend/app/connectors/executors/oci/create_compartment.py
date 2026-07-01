# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone
from ._client import get_identity_client


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    parent_compartment_id = (
        parameters.get("parent_compartment_id")
        or creds.get("compartment_id")
        or creds.get("tenancy_id")
        or creds.get("tenancy", "")
    )
    name = parameters.get("name", "nexplane-compartment")
    description = parameters.get("description", "Created by Nexplane")

    auto_asset = {
        "name": name,
        "asset_type": "cloud_account",
        "environment": "prod",
        "criticality": "medium",
        "asset_metadata": {
            "name": name,
            "parent_compartment_id": parent_compartment_id,
            "provider": "oci",
        },
        "tags": ["oci", "compartment", "nexplane-managed"],
    }

    if not creds:
        return {
            "action": "create_compartment",
            "name": name,
            "compartment_id": "ocid1.compartment.oc1..mock",
            "mock": True,
            "_auto_asset": auto_asset,
        }

    client = get_identity_client(creds)
    loop = asyncio.get_running_loop()

    def _call():
        import oci
        details = oci.identity.models.CreateCompartmentDetails(
            compartment_id=parent_compartment_id,
            name=name,
            description=description,
        )
        return client.create_compartment(details).data

    compartment = await loop.run_in_executor(None, _call)
    auto_asset["asset_metadata"]["compartment_id"] = compartment.id
    return {
        "action": "create_compartment",
        "name": name,
        "compartment_id": compartment.id,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "_auto_asset": auto_asset,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.oci.delete_compartment import execute as delete
    return await delete(
        {"compartment_id": execution_result.get("compartment_id"), "name": execution_result.get("name")},
        [],
        connector,
    )
