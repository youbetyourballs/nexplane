# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})

    compartment_id = parameters.get("compartment_id", "")
    display_name = parameters.get("display_name", "nexplane-vcn")
    cidr_block = parameters.get("cidr_block", "10.0.0.0/16")
    dns_label = parameters.get("dns_label", "nexplanevcn")

    auto_asset = {
        "name": display_name,
        "asset_type": "application",
        "environment": "prod",
        "criticality": "medium",
        "asset_metadata": {
            "vcn_id": "",
            "cidr_block": cidr_block,
            "compartment_id": compartment_id,
            "internet_gateway_id": "",
            "provider": "oci",
            "lifecycle_state": "AVAILABLE",
        },
        "tags": ["oci", "oci-vcn", "nexplane-managed"],
    }

    if not creds:
        auto_asset["asset_metadata"]["vcn_id"] = "ocid1.vcn.oc1..mock"
        auto_asset["asset_metadata"]["internet_gateway_id"] = "ocid1.internetgateway.oc1..mock"
        return {
            "action": "create_vcn",
            "vcn_id": "ocid1.vcn.oc1..mock",
            "internet_gateway_id": "ocid1.internetgateway.oc1..mock",
            "cidr_block": cidr_block,
            "mock": True,
            "_auto_asset": auto_asset,
        }

    from ._client import get_network_client
    import oci

    tenancy_id = creds["tenancy"]
    if not compartment_id:
        compartment_id = tenancy_id

    network = get_network_client(creds)
    loop = asyncio.get_running_loop()

    vcn_details = oci.core.models.CreateVcnDetails(
        compartment_id=compartment_id,
        display_name=display_name,
        cidr_block=cidr_block,
        dns_label=dns_label,
        freeform_tags={"managed-by": "nexplane"},
    )
    vcn = await loop.run_in_executor(None, lambda: network.create_vcn(vcn_details).data)
    vcn_id = vcn.id

    ig_details = oci.core.models.CreateInternetGatewayDetails(
        compartment_id=compartment_id,
        vcn_id=vcn_id,
        display_name=f"{display_name}-igw",
        is_enabled=True,
        freeform_tags={"managed-by": "nexplane"},
    )
    igw = await loop.run_in_executor(None, lambda: network.create_internet_gateway(ig_details).data)
    igw_id = igw.id

    route_rules = [
        oci.core.models.RouteRule(
            destination="0.0.0.0/0",
            destination_type="CIDR_BLOCK",
            network_entity_id=igw_id,
        )
    ]
    update_rt_details = oci.core.models.UpdateRouteTableDetails(route_rules=route_rules)
    await loop.run_in_executor(
        None,
        lambda: network.update_route_table(vcn.default_route_table_id, update_rt_details),
    )

    auto_asset["asset_metadata"]["vcn_id"] = vcn_id
    auto_asset["asset_metadata"]["internet_gateway_id"] = igw_id

    return {
        "action": "create_vcn",
        "vcn_id": vcn_id,
        "internet_gateway_id": igw_id,
        "cidr_block": cidr_block,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "_auto_asset": auto_asset,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    vcn_id = execution_result.get("vcn_id", "")
    igw_id = execution_result.get("internet_gateway_id", "")

    if not vcn_id or not creds:
        return {"rolled_back": False, "reason": "no vcn_id to delete"}

    from ._client import get_network_client
    import oci

    network = get_network_client(creds)
    loop = asyncio.get_running_loop()

    if igw_id:
        try:
            vcn = await loop.run_in_executor(None, lambda: network.get_vcn(vcn_id).data)
            await loop.run_in_executor(
                None,
                lambda: network.update_route_table(
                    vcn.default_route_table_id,
                    oci.core.models.UpdateRouteTableDetails(route_rules=[]),
                ),
            )
        except Exception:
            pass
        await loop.run_in_executor(None, lambda: network.delete_internet_gateway(igw_id))

    await loop.run_in_executor(None, lambda: network.delete_vcn(vcn_id))
    return {"rolled_back": True, "vcn_id": vcn_id}
