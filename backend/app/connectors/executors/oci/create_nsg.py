import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    compartment_id = parameters.get("compartment_id", "")
    vcn_id = parameters.get("vcn_id", "")
    display_name = parameters.get("display_name", "nexplane-nsg")

    auto_asset = {
        "name": display_name,
        "asset_type": "firewall",
        "environment": "prod",
        "criticality": "medium",
        "asset_metadata": {
            "nsg_id": "ocid1.networksecuritygroup.mock",
            "vcn_id": vcn_id or "ocid1.vcn.mock",
            "compartment_id": compartment_id or "ocid1.compartment.mock",
            "lifecycle_state": "AVAILABLE",
            "provider": "oci",
        },
        "tags": ["oci", "oci-nsg"],
    }

    if not creds:
        return {
            "action": "create_nsg",
            "nsg_id": "ocid1.networksecuritygroup.mock",
            "display_name": display_name,
            "mock": True,
            "_auto_asset": auto_asset,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_network_client
    import oci as oci_sdk
    loop = asyncio.get_running_loop()
    network = get_network_client(creds)
    comp_id = compartment_id or creds.get("tenancy", "")

    details = oci_sdk.core.models.CreateNetworkSecurityGroupDetails(
        compartment_id=comp_id,
        vcn_id=vcn_id,
        display_name=display_name,
    )
    nsg = await loop.run_in_executor(
        None, lambda: network.create_network_security_group(details).data
    )

    auto_asset["asset_metadata"]["nsg_id"] = nsg.id
    auto_asset["asset_metadata"]["vcn_id"] = nsg.vcn_id
    auto_asset["asset_metadata"]["compartment_id"] = nsg.compartment_id
    auto_asset["asset_metadata"]["lifecycle_state"] = nsg.lifecycle_state

    return {
        "action": "create_nsg",
        "nsg_id": nsg.id,
        "display_name": nsg.display_name,
        "vcn_id": nsg.vcn_id,
        "compartment_id": nsg.compartment_id,
        "_auto_asset": auto_asset,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.oci.delete_nsg import execute as delete
    return await delete(
        {"nsg_id": execution_result.get("nsg_id")},
        [],
        connector,
    )
