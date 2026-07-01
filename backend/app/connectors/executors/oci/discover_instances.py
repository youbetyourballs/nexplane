# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    compartment_id = parameters.get("compartment_id", "")

    if not creds:
        return {
            "action": "discover_instances",
            "assets": [
                {
                    "name": "mock-oci-instance",
                    "asset_type": "server",
                    "asset_metadata": {
                        "instance_id": "ocid1.instance.oc1..mock",
                        "shape": "VM.Standard.E2.1.Micro",
                        "region": "us-ashburn-1",
                        "compartment_id": compartment_id or "ocid1.tenancy.oc1..mock",
                        "lifecycle_state": "RUNNING",
                        "private_ip": "10.0.0.10",
                        "image_id": "ocid1.image.oc1..mock",
                    },
                    "tags": ["oci", "compute"],
                }
            ],
            "count": 1,
            "mock": True,
            "discovered_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_compute_client, get_network_client

    region = creds["region"]
    tenancy_id = creds["tenancy"]
    if not compartment_id:
        compartment_id = tenancy_id

    compute = get_compute_client(creds)
    network = get_network_client(creds)
    loop = asyncio.get_running_loop()

    instances_data = await loop.run_in_executor(
        None,
        lambda: compute.list_instances(compartment_id).data,
    )

    assets = []
    for inst in instances_data:
        if inst.lifecycle_state == "TERMINATED":
            continue

        private_ip = None
        try:
            vnic_attachments = await loop.run_in_executor(
                None,
                lambda i=inst: compute.list_vnic_attachments(
                    compartment_id=compartment_id, instance_id=i.id
                ).data,
            )
            if vnic_attachments:
                vnic = await loop.run_in_executor(
                    None,
                    lambda va=vnic_attachments[0]: network.get_vnic(va.vnic_id).data,
                )
                private_ip = vnic.private_ip
        except Exception:
            pass

        display_name = inst.display_name or inst.id.split(".")[-1]
        assets.append({
            "name": display_name,
            "asset_type": "server",
            "asset_metadata": {
                "instance_id": inst.id,
                "shape": inst.shape,
                "region": region,
                "compartment_id": compartment_id,
                "lifecycle_state": inst.lifecycle_state,
                "private_ip": private_ip,
                "image_id": inst.image_id,
            },
            "tags": ["oci", "compute"],
        })

    return {
        "action": "discover_instances",
        "assets": assets,
        "count": len(assets),
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
