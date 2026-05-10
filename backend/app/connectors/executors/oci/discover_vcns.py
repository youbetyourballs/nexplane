import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    compartment_id = parameters.get("compartment_id", "")

    if not creds:
        return {
            "action": "discover_vcns",
            "assets": [
                {
                    "name": "mock-vcn",
                    "asset_type": "application",
                    "asset_metadata": {
                        "vcn_id": "ocid1.vcn.oc1..mock",
                        "cidr_block": "10.0.0.0/16",
                        "compartment_id": compartment_id or "ocid1.tenancy.oc1..mock",
                        "lifecycle_state": "AVAILABLE",
                        "provider": "oci",
                    },
                    "tags": ["oci", "oci-vcn"],
                },
                {
                    "name": "mock-subnet",
                    "asset_type": "application",
                    "asset_metadata": {
                        "subnet_id": "ocid1.subnet.oc1..mock",
                        "vcn_id": "ocid1.vcn.oc1..mock",
                        "cidr_block": "10.0.0.0/24",
                        "compartment_id": compartment_id or "ocid1.tenancy.oc1..mock",
                        "lifecycle_state": "AVAILABLE",
                        "provider": "oci",
                    },
                    "tags": ["oci", "oci-subnet"],
                },
            ],
            "count": 2,
            "mock": True,
            "discovered_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_network_client

    region = creds["region"]
    tenancy_id = creds["tenancy"]
    if not compartment_id:
        compartment_id = tenancy_id

    network = get_network_client(creds)
    loop = asyncio.get_running_loop()

    vcns_data = await loop.run_in_executor(None, lambda: network.list_vcns(compartment_id).data)
    subnets_data = await loop.run_in_executor(None, lambda: network.list_subnets(compartment_id).data)

    assets = []

    for vcn in vcns_data:
        if vcn.lifecycle_state == "TERMINATED":
            continue
        assets.append({
            "name": vcn.display_name or vcn.id.split(".")[-1],
            "asset_type": "application",
            "asset_metadata": {
                "vcn_id": vcn.id,
                "cidr_block": vcn.cidr_block,
                "compartment_id": compartment_id,
                "region": region,
                "lifecycle_state": vcn.lifecycle_state,
                "provider": "oci",
            },
            "tags": ["oci", "oci-vcn"],
        })

    for subnet in subnets_data:
        if subnet.lifecycle_state == "TERMINATED":
            continue
        assets.append({
            "name": subnet.display_name or subnet.id.split(".")[-1],
            "asset_type": "application",
            "asset_metadata": {
                "subnet_id": subnet.id,
                "vcn_id": subnet.vcn_id,
                "cidr_block": subnet.cidr_block,
                "compartment_id": compartment_id,
                "region": region,
                "lifecycle_state": subnet.lifecycle_state,
                "provider": "oci",
            },
            "tags": ["oci", "oci-subnet"],
        })

    return {
        "action": "discover_vcns",
        "assets": assets,
        "count": len(assets),
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
