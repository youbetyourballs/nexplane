import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    compartment_id = parameters.get("compartment_id", "")

    if not creds:
        return {
            "action": "discover_load_balancers",
            "assets": [
                {
                    "name": "mock-oci-lb",
                    "asset_type": "load_balancer",
                    "environment": "prod",
                    "criticality": "high",
                    "asset_metadata": {
                        "load_balancer_id": "ocid1.loadbalancer.mock",
                        "shape_name": "flexible",
                        "shape_min_mbps": 10,
                        "shape_max_mbps": 100,
                        "ip_addresses": ["203.0.113.1"],
                        "lifecycle_state": "ACTIVE",
                        "compartment_id": compartment_id or "ocid1.compartment.mock",
                        "provider": "oci",
                    },
                    "tags": ["oci", "load-balancer"],
                }
            ],
            "mock": True,
            "discovered_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_loadbalancer_client
    loop = asyncio.get_running_loop()
    lb_client = get_loadbalancer_client(creds)
    comp_id = compartment_id or creds.get("tenancy", "")

    lbs = await loop.run_in_executor(
        None, lambda: lb_client.list_load_balancers(compartment_id=comp_id).data
    )

    assets = []
    for lb in lbs:
        ip_addrs = [ip.ip_address for ip in (lb.ip_addresses or [])]
        shape = lb.shape_details
        assets.append({
            "name": lb.display_name,
            "asset_type": "load_balancer",
            "environment": "prod",
            "criticality": "high",
            "asset_metadata": {
                "load_balancer_id": lb.id,
                "shape_name": lb.shape_name,
                "shape_min_mbps": shape.minimum_bandwidth_in_mbps if shape else None,
                "shape_max_mbps": shape.maximum_bandwidth_in_mbps if shape else None,
                "ip_addresses": ip_addrs,
                "lifecycle_state": lb.lifecycle_state,
                "compartment_id": lb.compartment_id,
                "provider": "oci",
            },
            "tags": ["oci", "load-balancer"],
            "_dedup_key": lb.id,
            "_dedup_field": "load_balancer_id",
        })

    return {
        "action": "discover_load_balancers",
        "assets": assets,
        "count": len(assets),
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }
