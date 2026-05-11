import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    nsg_id = parameters.get("nsg_id", "")
    rule_id = parameters.get("rule_id", "")
    # If rule_id is provided, use it directly; otherwise match by direction/protocol/port
    direction = parameters.get("direction", "INGRESS")
    protocol = parameters.get("protocol", "6")
    source = parameters.get("source", "0.0.0.0/0")
    destination = parameters.get("destination", "0.0.0.0/0")
    port_min = int(parameters.get("port_min", 443))
    port_max = int(parameters.get("port_max", 443))

    if not creds:
        return {
            "action": "remove_nsg_rule",
            "nsg_id": nsg_id or "ocid1.networksecuritygroup.mock",
            "rule_id": rule_id or "mock-rule-id",
            "removed": True,
            "mock": True,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_network_client
    import oci as oci_sdk
    loop = asyncio.get_running_loop()
    network = get_network_client(creds)

    if not rule_id:
        # Resolve rule_id by listing rules and matching
        rules_resp = await loop.run_in_executor(
            None, lambda: network.list_network_security_group_security_rules(nsg_id).data
        )

        def _matches(r):
            if r.direction != direction or r.protocol != protocol:
                return False
            if direction == "INGRESS" and r.source != source:
                return False
            if direction == "EGRESS" and r.destination != destination:
                return False
            if protocol == "6" and r.tcp_options:
                rng = r.tcp_options.destination_port_range
                return rng and rng.min == port_min and rng.max == port_max
            return True

        match = next((r for r in rules_resp if _matches(r)), None)
        rule_id = match.id if match else None

    if not rule_id:
        return {
            "action": "remove_nsg_rule",
            "nsg_id": nsg_id,
            "removed": False,
            "reason": "rule not found",
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    remove_details = oci_sdk.core.models.RemoveNetworkSecurityGroupSecurityRulesDetails(
        security_rule_ids=[rule_id]
    )
    await loop.run_in_executor(
        None, lambda: network.remove_network_security_group_security_rules(nsg_id, remove_details)
    )

    return {
        "action": "remove_nsg_rule",
        "nsg_id": nsg_id,
        "rule_id": rule_id,
        "removed": True,
        "removed_rule": {
            "direction": direction,
            "protocol": protocol,
            "source": source if direction == "INGRESS" else None,
            "destination": destination if direction == "EGRESS" else None,
            "port_min": port_min,
            "port_max": port_max,
        },
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.oci.add_nsg_rule import execute as add
    removed = execution_result.get("removed_rule", {})
    return await add(
        {
            "nsg_id": execution_result.get("nsg_id", parameters.get("nsg_id")),
            "direction": removed.get("direction", parameters.get("direction", "INGRESS")),
            "protocol": removed.get("protocol", parameters.get("protocol", "6")),
            "source": removed.get("source", parameters.get("source", "0.0.0.0/0")),
            "destination": removed.get("destination", parameters.get("destination", "0.0.0.0/0")),
            "port_min": removed.get("port_min", parameters.get("port_min", 443)),
            "port_max": removed.get("port_max", parameters.get("port_max", 443)),
        },
        [],
        connector,
    )
