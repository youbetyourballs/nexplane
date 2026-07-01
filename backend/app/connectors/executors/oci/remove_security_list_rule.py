# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    security_list_id = parameters.get("security_list_id", "")
    direction = parameters.get("direction", "INGRESS")
    protocol = parameters.get("protocol", "6")
    source = parameters.get("source", "0.0.0.0/0")
    destination = parameters.get("destination", "0.0.0.0/0")
    port_min = int(parameters.get("port_min", 22))
    port_max = int(parameters.get("port_max", 22))

    if not creds:
        return {
            "action": "remove_security_list_rule",
            "security_list_id": security_list_id or "ocid1.securitylist.mock",
            "direction": direction,
            "removed": True,
            "mock": True,
            "removed_rule": {"direction": direction, "protocol": protocol, "port_min": port_min, "port_max": port_max},
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_network_client
    import oci as oci_sdk
    loop = asyncio.get_running_loop()
    network = get_network_client(creds)

    sl = await loop.run_in_executor(
        None, lambda: network.get_security_list(security_list_id).data
    )

    removed_rule = None

    def _matches_ingress(rule):
        if rule.protocol != protocol:
            return False
        if rule.source != source:
            return False
        if protocol == "6" and rule.tcp_options:
            rng = rule.tcp_options.destination_port_range
            if rng and rng.min == port_min and rng.max == port_max:
                return True
            return False
        return True

    def _matches_egress(rule):
        if rule.protocol != protocol:
            return False
        if rule.destination != destination:
            return False
        if protocol == "6" and rule.tcp_options:
            rng = rule.tcp_options.destination_port_range
            if rng and rng.min == port_min and rng.max == port_max:
                return True
            return False
        return True

    if direction == "INGRESS":
        old_rules = list(sl.ingress_security_rules or [])
        new_rules = [r for r in old_rules if not _matches_ingress(r)]
        removed_rule = next((r for r in old_rules if _matches_ingress(r)), None)
        details = oci_sdk.core.models.UpdateSecurityListDetails(
            ingress_security_rules=new_rules,
            egress_security_rules=list(sl.egress_security_rules or []),
        )
    else:
        old_rules = list(sl.egress_security_rules or [])
        new_rules = [r for r in old_rules if not _matches_egress(r)]
        removed_rule = next((r for r in old_rules if _matches_egress(r)), None)
        details = oci_sdk.core.models.UpdateSecurityListDetails(
            ingress_security_rules=list(sl.ingress_security_rules or []),
            egress_security_rules=new_rules,
        )

    await loop.run_in_executor(
        None, lambda: network.update_security_list(security_list_id, details)
    )

    return {
        "action": "remove_security_list_rule",
        "security_list_id": security_list_id,
        "direction": direction,
        "removed": removed_rule is not None,
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
    """Rollback by re-adding the removed rule."""
    from app.connectors.executors.oci.add_security_list_rule import execute as add
    removed = execution_result.get("removed_rule", {})
    return await add(
        {
            "security_list_id": execution_result.get("security_list_id", parameters.get("security_list_id")),
            "direction": removed.get("direction", parameters.get("direction", "INGRESS")),
            "protocol": removed.get("protocol", parameters.get("protocol", "6")),
            "source": removed.get("source", parameters.get("source", "0.0.0.0/0")),
            "destination": removed.get("destination", parameters.get("destination", "0.0.0.0/0")),
            "port_min": removed.get("port_min", parameters.get("port_min", 22)),
            "port_max": removed.get("port_max", parameters.get("port_max", 22)),
        },
        [],
        connector,
    )
