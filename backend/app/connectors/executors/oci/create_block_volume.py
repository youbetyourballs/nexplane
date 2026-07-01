# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    compartment_id = parameters.get("compartment_id", creds.get("compartment_id", "") if creds else "ocid1.compartment.oc1..mock")
    display_name = parameters.get("display_name", "nexplane-volume")
    size_in_gbs = parameters.get("size_in_gbs", 50)
    vpus_per_gb = parameters.get("vpus_per_gb", 10)
    region = (creds.get("region", "us-ashburn-1") if creds else "us-ashburn-1")

    auto_asset = {
        "name": display_name,
        "asset_type": "storage_bucket",
        "environment": "prod",
        "criticality": "medium",
        "asset_metadata": {
            "volume_id": "",
            "compartment_id": compartment_id,
            "size_in_gbs": size_in_gbs,
            "lifecycle_state": "PROVISIONING",
            "vpus_per_gb": vpus_per_gb,
            "region": region,
        },
        "tags": ["oci", "block-volume", "nexplane-managed"],
    }

    if not creds:
        auto_asset["asset_metadata"]["volume_id"] = "ocid1.volume.oc1..mock"
        return {
            "action": "create_block_volume",
            "volume_id": "ocid1.volume.oc1..mock",
            "display_name": display_name,
            "mock": True,
            "_auto_asset": auto_asset,
        }

    from ._client import get_blockstorage_client, get_oci_config
    import oci
    client = get_blockstorage_client(creds)
    loop = asyncio.get_running_loop()

    def _call():
        # Resolve availability domain if not provided
        availability_domain = parameters.get("availability_domain")
        if not availability_domain:
            config = get_oci_config(creds)
            identity_client = oci.identity.IdentityClient(config)
            ads = identity_client.list_availability_domains(compartment_id=compartment_id).data
            availability_domain = ads[0].name if ads else "AD-1"

        details = oci.core.models.CreateVolumeDetails(
            compartment_id=compartment_id,
            display_name=display_name,
            size_in_gbs=size_in_gbs,
            vpus_per_gb=vpus_per_gb,
            availability_domain=availability_domain,
        )
        volume = client.create_volume(create_volume_details=details).data
        return volume

    volume = await loop.run_in_executor(None, _call)
    auto_asset["asset_metadata"]["volume_id"] = volume.id
    auto_asset["asset_metadata"]["lifecycle_state"] = volume.lifecycle_state

    return {
        "action": "create_block_volume",
        "volume_id": volume.id,
        "display_name": display_name,
        "size_in_gbs": size_in_gbs,
        "lifecycle_state": volume.lifecycle_state,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "_auto_asset": auto_asset,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.oci.delete_block_volume import execute as delete
    return await delete({"volume_id": execution_result.get("volume_id", "")}, [], connector)
