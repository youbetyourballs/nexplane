import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})

    compartment_id = parameters.get("compartment_id", "")
    vcn_id = parameters.get("vcn_id", "")
    display_name = parameters.get("display_name", "nexplane-subnet")
    cidr_block = parameters.get("cidr_block", "10.0.0.0/24")
    dns_label = parameters.get("dns_label", "nexplanesubnet")
    prohibit_public_ip = parameters.get("prohibit_public_ip_on_vnic", False)

    auto_asset = {
        "name": display_name,
        "asset_type": "application",
        "environment": "prod",
        "criticality": "medium",
        "asset_metadata": {
            "subnet_id": "",
            "vcn_id": vcn_id,
            "cidr_block": cidr_block,
            "compartment_id": compartment_id,
            "security_list_id": "",
            "provider": "oci",
            "lifecycle_state": "AVAILABLE",
        },
        "tags": ["oci", "oci-subnet", "nexplane-managed"],
    }

    if not creds:
        auto_asset["asset_metadata"]["subnet_id"] = "ocid1.subnet.oc1..mock"
        auto_asset["asset_metadata"]["security_list_id"] = "ocid1.securitylist.oc1..mock"
        return {
            "action": "create_subnet",
            "subnet_id": "ocid1.subnet.oc1..mock",
            "vcn_id": vcn_id,
            "security_list_id": "ocid1.securitylist.oc1..mock",
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

    if not vcn_id:
        vcns = await loop.run_in_executor(None, lambda: network.list_vcns(compartment_id).data)
        available_vcns = [v for v in vcns if v.lifecycle_state == "AVAILABLE"]
        if not available_vcns:
            raise ValueError("No available VCN in this compartment. Run oci_vcn_create first.")
        vcn_id = available_vcns[0].id

    tcp = "6"
    icmp = "1"
    ingress_rules = [
        oci.core.models.IngressSecurityRule(
            protocol=tcp,
            source="0.0.0.0/0",
            source_type="CIDR_BLOCK",
            tcp_options=oci.core.models.TcpOptions(
                destination_port_range=oci.core.models.PortRange(min=22, max=22)
            ),
        ),
        oci.core.models.IngressSecurityRule(
            protocol=icmp,
            source="0.0.0.0/0",
            source_type="CIDR_BLOCK",
            icmp_options=oci.core.models.IcmpOptions(type=3, code=4),
        ),
    ]
    egress_rules = [
        oci.core.models.EgressSecurityRule(
            protocol="all",
            destination="0.0.0.0/0",
            destination_type="CIDR_BLOCK",
        )
    ]
    sl_details = oci.core.models.CreateSecurityListDetails(
        compartment_id=compartment_id,
        vcn_id=vcn_id,
        display_name=f"{display_name}-seclist",
        ingress_security_rules=ingress_rules,
        egress_security_rules=egress_rules,
        freeform_tags={"managed-by": "nexplane"},
    )
    security_list = await loop.run_in_executor(None, lambda: network.create_security_list(sl_details).data)
    security_list_id = security_list.id

    subnet_details = oci.core.models.CreateSubnetDetails(
        compartment_id=compartment_id,
        vcn_id=vcn_id,
        display_name=display_name,
        cidr_block=cidr_block,
        dns_label=dns_label,
        prohibit_public_ip_on_vnic=prohibit_public_ip,
        security_list_ids=[security_list_id],
        freeform_tags={"managed-by": "nexplane"},
    )
    subnet = await loop.run_in_executor(None, lambda: network.create_subnet(subnet_details).data)
    subnet_id = subnet.id

    auto_asset["asset_metadata"]["subnet_id"] = subnet_id
    auto_asset["asset_metadata"]["vcn_id"] = vcn_id
    auto_asset["asset_metadata"]["security_list_id"] = security_list_id

    return {
        "action": "create_subnet",
        "subnet_id": subnet_id,
        "vcn_id": vcn_id,
        "security_list_id": security_list_id,
        "cidr_block": cidr_block,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "_auto_asset": auto_asset,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    subnet_id = execution_result.get("subnet_id", "")
    security_list_id = execution_result.get("security_list_id", "")

    if not subnet_id or not creds:
        return {"rolled_back": False, "reason": "no subnet_id to delete"}

    from ._client import get_network_client

    network = get_network_client(creds)
    loop = asyncio.get_running_loop()

    await loop.run_in_executor(None, lambda: network.delete_subnet(subnet_id))
    if security_list_id:
        try:
            await loop.run_in_executor(None, lambda: network.delete_security_list(security_list_id))
        except Exception:
            pass
    return {"rolled_back": True, "subnet_id": subnet_id}
