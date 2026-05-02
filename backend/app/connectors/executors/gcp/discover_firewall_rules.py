import asyncio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_firewall_rules", "rules": [
            {"name": "default-allow-ssh", "direction": "INGRESS", "action": "ALLOW", "source_ranges": ["0.0.0.0/0"], "ports": ["tcp:22"]}
        ], "count": 1}
    from ._client import get_credentials, get_project_id
    from google.cloud import compute_v1
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()
    client = compute_v1.FirewallsClient(credentials=credentials)
    fw_list = await loop.run_in_executor(None, lambda: list(client.list(project=project)))
    rules = []
    for fw in fw_list:
        ports = [f"{a.ip_protocol}:{','.join(a.ports)}" for a in fw.allowed] + \
                [f"{d.ip_protocol}:{','.join(d.ports)}" for d in fw.denied]
        rules.append({
            "name": fw.name,
            "direction": fw.direction,
            "source_ranges": list(fw.source_ranges),
            "target_tags": list(fw.target_tags),
            "ports": ports,
            "disabled": fw.disabled,
        })
    return {"action": "discover_firewall_rules", "rules": rules, "count": len(rules)}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
