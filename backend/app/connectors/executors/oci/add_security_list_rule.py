import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    security_list_id = parameters.get("security_list_id", "")
    direction = parameters.get("direction", "INGRESS")
    protocol = parameters.get("protocol", "6")  # TCP
    source = parameters.get("source", "0.0.0.0/0")
    destination = parameters.get("destination", "0.0.0.0/0")
    port_min = int(parameters.get("port_min", 22))
    port_max = int(parameters.get("port_max", 22))
    description = parameters.get("description", "nexplane-rule")

    if not creds:
        return {
            "action": "add_security_list_rule",
            "security_list_id": security_list_id or "ocid1.securitylist.mock",
            "direction": direction,
            "protocol": protocol,
            "source": source if direction == "INGRESS" else None,
            "destination": destination if direction == "EGRESS" else None,
            "port_min": port_min,
            "port_max": port_max,
            "mock": True,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_network_client
    import oci as oci_sdk
    loop = asyncio.get_running_loop()
    network = get_network_client(creds)

    # Fetch existing security list
    sl = await loop.run_in_executor(
        None, lambda: network.get_security_list(security_list_id).data
    )

    tcp_options = oci_sdk.core.models.TcpOptions(
        destination_port_range=oci_sdk.core.models.PortRange(min=port_min, max=port_max)
    )

    if direction == "INGRESS":
        new_rule = oci_sdk.core.models.IngressSecurityRule(
            protocol=protocol,
            source=source,
            source_type="CIDR_BLOCK",
            tcp_options=tcp_options if protocol == "6" else None,
            description=description,
            is_stateless=False,
        )
        updated_ingress = list(sl.ingress_security_rules or []) + [new_rule]
        details = oci_sdk.core.models.UpdateSecurityListDetails(
            ingress_security_rules=updated_ingress,
            egress_security_rules=list(sl.egress_security_rules or []),
        )
    else:
        new_rule = oci_sdk.core.models.EgressSecurityRule(
            protocol=protocol,
            destination=destination,
            destination_type="CIDR_BLOCK",
            tcp_options=tcp_options if protocol == "6" else None,
            description=description,
            is_stateless=False,
        )
        updated_egress = list(sl.egress_security_rules or []) + [new_rule]
        details = oci_sdk.core.models.UpdateSecurityListDetails(
            ingress_security_rules=list(sl.ingress_security_rules or []),
            egress_security_rules=updated_egress,
        )

    await loop.run_in_executor(
        None, lambda: network.update_security_list(security_list_id, details)
    )

    return {
        "action": "add_security_list_rule",
        "security_list_id": security_list_id,
        "direction": direction,
        "protocol": protocol,
        "source": source if direction == "INGRESS" else None,
        "destination": destination if direction == "EGRESS" else None,
        "port_min": port_min,
        "port_max": port_max,
        "description": description,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Rollback by removing the added rule."""
    from app.connectors.executors.oci.remove_security_list_rule import execute as remove
    return await remove(
        {
            "security_list_id": execution_result.get("security_list_id", parameters.get("security_list_id")),
            "direction": execution_result.get("direction", parameters.get("direction", "INGRESS")),
            "protocol": execution_result.get("protocol", parameters.get("protocol", "6")),
            "source": execution_result.get("source", parameters.get("source", "0.0.0.0/0")),
            "destination": execution_result.get("destination", parameters.get("destination", "0.0.0.0/0")),
            "port_min": execution_result.get("port_min", parameters.get("port_min", 22)),
            "port_max": execution_result.get("port_max", parameters.get("port_max", 22)),
        },
        [],
        connector,
    )
