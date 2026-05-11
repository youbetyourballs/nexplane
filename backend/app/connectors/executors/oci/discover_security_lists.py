import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    compartment_id = parameters.get("compartment_id", "")

    if not creds:
        return {
            "action": "discover_security_lists",
            "assets": [
                {
                    "name": "mock-security-list",
                    "asset_type": "firewall",
                    "environment": "prod",
                    "criticality": "medium",
                    "asset_metadata": {
                        "security_list_id": "ocid1.securitylist.mock",
                        "vcn_id": "ocid1.vcn.mock",
                        "compartment_id": compartment_id or "ocid1.compartment.mock",
                        "lifecycle_state": "AVAILABLE",
                        "ingress_rule_count": 2,
                        "egress_rule_count": 1,
                    },
                    "tags": ["oci", "security-list"],
                }
            ],
            "mock": True,
            "discovered_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_network_client, get_compartment_id
    loop = asyncio.get_running_loop()
    network = get_network_client(creds)
    comp_id = compartment_id or get_compartment_id(creds)

    security_lists = await loop.run_in_executor(
        None, lambda: network.list_security_lists(compartment_id=comp_id).data
    )

    assets = []
    for sl in security_lists:
        assets.append({
            "name": sl.display_name,
            "asset_type": "firewall",
            "environment": "prod",
            "criticality": "medium",
            "asset_metadata": {
                "security_list_id": sl.id,
                "vcn_id": sl.vcn_id,
                "compartment_id": sl.compartment_id,
                "lifecycle_state": sl.lifecycle_state,
                "ingress_rule_count": len(sl.ingress_security_rules or []),
                "egress_rule_count": len(sl.egress_security_rules or []),
            },
            "tags": ["oci", "security-list"],
            "_dedup_key": sl.id,
            "_dedup_field": "security_list_id",
        })

    return {
        "action": "discover_security_lists",
        "assets": assets,
        "count": len(assets),
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }
