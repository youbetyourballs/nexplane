# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    nsg_id = parameters.get("nsg_id", "")
    direction = parameters.get("direction", "INGRESS")
    protocol = parameters.get("protocol", "6")
    source = parameters.get("source", "0.0.0.0/0")
    destination = parameters.get("destination", "0.0.0.0/0")
    port_min = int(parameters.get("port_min", 443))
    port_max = int(parameters.get("port_max", 443))
    description = parameters.get("description", "nexplane-nsg-rule")

    if not creds:
        return {
            "action": "add_nsg_rule",
            "nsg_id": nsg_id or "ocid1.networksecuritygroup.mock",
            "direction": direction,
            "protocol": protocol,
            "port_min": port_min,
            "port_max": port_max,
            "rule_id": "mock-rule-id",
            "mock": True,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_network_client
    import oci as oci_sdk
    loop = asyncio.get_running_loop()
    network = get_network_client(creds)

    tcp_options = oci_sdk.core.models.TcpOptions(
        destination_port_range=oci_sdk.core.models.PortRange(min=port_min, max=port_max)
    ) if protocol == "6" else None

    if direction == "INGRESS":
        rule = oci_sdk.core.models.AddSecurityRuleDetails(
            direction="INGRESS",
            protocol=protocol,
            source=source,
            source_type="CIDR_BLOCK",
            tcp_options=tcp_options,
            description=description,
            is_stateless=False,
        )
    else:
        rule = oci_sdk.core.models.AddSecurityRuleDetails(
            direction="EGRESS",
            protocol=protocol,
            destination=destination,
            destination_type="CIDR_BLOCK",
            tcp_options=tcp_options,
            description=description,
            is_stateless=False,
        )

    add_details = oci_sdk.core.models.AddNetworkSecurityGroupSecurityRulesDetails(
        security_rules=[rule]
    )
    result = await loop.run_in_executor(
        None, lambda: network.add_network_security_group_security_rules(nsg_id, add_details).data
    )

    rule_id = result.security_rules[0].id if result.security_rules else None

    return {
        "action": "add_nsg_rule",
        "nsg_id": nsg_id,
        "direction": direction,
        "protocol": protocol,
        "source": source if direction == "INGRESS" else None,
        "destination": destination if direction == "EGRESS" else None,
        "port_min": port_min,
        "port_max": port_max,
        "rule_id": rule_id,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.oci.remove_nsg_rule import execute as remove
    return await remove(
        {
            "nsg_id": execution_result.get("nsg_id", parameters.get("nsg_id")),
            "rule_id": execution_result.get("rule_id"),
        },
        [],
        connector,
    )
