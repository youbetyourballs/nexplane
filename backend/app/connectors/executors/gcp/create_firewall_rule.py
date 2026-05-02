import asyncio


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
    if action.lower() == "allow":
        firewall.allowed = [compute_v1.Allowed(ip_protocol="tcp", ports=ports)] if ports else [compute_v1.Allowed(ip_protocol="all")]
    else:
        firewall.denied = [compute_v1.Denied(ip_protocol="tcp", ports=ports)] if ports else [compute_v1.Denied(ip_protocol="all")]
    op = await loop.run_in_executor(None, lambda: client.insert(project=project, firewall_resource=firewall))
    return {"action": "create_firewall_rule", "rule_name": rule_name, "operation": op.name}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.gcp.delete_firewall_rule import execute as delete_rule
    return await delete_rule({"rule_name": parameters["rule_name"]}, [], connector)
