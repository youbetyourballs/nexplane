# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

ROLLBACK_CAPABILITY = "full"

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    rule_name = parameters["rule_name"]
    if not creds:
        return {"action": "create_firewall_rule", "rule_name": rule_name, "created": True}
    from ._client import get_credentials, get_project_id
    from google.cloud import compute_v1
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()
    client = compute_v1.FirewallsClient(credentials=credentials)
    direction = parameters.get("direction", "INGRESS")
    action = parameters.get("action", "allow")
    source_ranges = parameters.get("source_ranges", [])
    ports = parameters.get("ports", [])
    firewall = compute_v1.Firewall(
        name=rule_name,
        direction=direction,
        source_ranges=source_ranges,
    )
    network = parameters.get("network", "global/networks/default")
    firewall.network = f"https://www.googleapis.com/compute/v1/projects/{project}/{network}"
    priority = parameters.get("priority", 1000)
    firewall.priority = priority
    allowed_rules = parameters.get("allowed", [])
    if action.lower() == "allow":
        if allowed_rules:
            firewall.allowed = [compute_v1.Allowed(I_p_protocol=r.get("IPProtocol", "tcp"), ports=r.get("ports", [])) for r in allowed_rules]
        elif ports:
            firewall.allowed = [compute_v1.Allowed(I_p_protocol="tcp", ports=ports)]
        else:
            firewall.allowed = [compute_v1.Allowed(I_p_protocol="all")]
    else:
        if allowed_rules:
            firewall.denied = [compute_v1.Denied(I_p_protocol=r.get("IPProtocol", "tcp"), ports=r.get("ports", [])) for r in allowed_rules]
        elif ports:
            firewall.denied = [compute_v1.Denied(I_p_protocol="tcp", ports=ports)]
        else:
            firewall.denied = [compute_v1.Denied(I_p_protocol="all")]
    op = await loop.run_in_executor(None, lambda: client.insert(project=project, firewall_resource=firewall))
    return {"action": "create_firewall_rule", "rule_name": rule_name, "operation": op.name}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.gcp.delete_firewall_rule import execute as delete_rule
    return await delete_rule({"rule_name": parameters["rule_name"]}, [], connector)
